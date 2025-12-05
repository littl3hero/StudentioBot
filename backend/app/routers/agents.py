# app/routers/agents.py
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.deps import settings
from app.agents import curator, examiner, materials_agent, orchestrator  # ← добавлен materials_agent и orchestrator


# Опционально используем LLM для извлечения goals/errors из диалога (с фолбэком)
try:
    from openai import OpenAI
    from openai import RateLimitError, AuthenticationError, APIConnectionError, APIStatusError
    _LLM_OK = bool(settings.OPENAI_API_KEY)
    _client = OpenAI(api_key=settings.OPENAI_API_KEY) if _LLM_OK else None
except Exception:
    _LLM_OK = False
    _client = None

router = APIRouter(prefix="/v1/agents", tags=["agents"])


# ====== Pydantic-схемы ======

Role = Literal["system", "user", "assistant"]


class ChatMsg(BaseModel):
    role: Role
    content: str

class PlanStep(BaseModel):
    id: str
    type: Literal["exam", "materials", "chat", "other"]
    title: str
    description: str
    meta: Dict[str, Any] = {}
    status: Literal["prepared", "pending", "error"] = "pending"


class OrchestratorBlock(BaseModel):
    instruction_message: str
    plan_steps: List[PlanStep] = []

    # Какому агенту «передать ход» дальше:
    # - examiner  → страница с тестами
    # - materials → страница с материалами
    # - curator   → остаться в чате куратора
    # - none      → никуда автоматически не идти
    next_agent: Optional[Literal["examiner", "materials", "curator", "none"]] = "none"

    # Какой фронтовый route стоит открыть следующем (если не None)
    # Например: "/tests" или "/materials"
    auto_route: Optional[str] = None

    # ID шага плана, который считается «главным» (первый приоритет)
    primary_step_id: Optional[str] = None



class CuratorFromChatRequest(BaseModel):
    student_id: str = "default"
    level: Literal["beginner", "intermediate", "advanced"] = "beginner"
    topic: str = ""                      # тема из UI
    messages: List[ChatMsg]              # диалог
    make_exam: bool = False
    count: int = 5


class CuratorFromChatResponse(BaseModel):
    ok: bool
    topic: str
    goals: str
    errors: List[str]
    profile: Dict[str, Any]
    exam: Optional[Dict[str, Any]] = None
    orchestrator: Optional[OrchestratorBlock] = None


class ExaminerReq(BaseModel):
    student_id: str = "default"
    count: int = 5


class ExaminerResp(BaseModel):
    ok: bool
    questions: List[Dict[str, Any]]
    rubric: str


# ====== Утилиты ======

def _normalize_level(v: str) -> str:
    v = (v or "").strip().lower()
    if v in {"beginner", "intermediate", "advanced"}:
        return v
    if "нач" in v:
        return "beginner"
    if "сред" in v:
        return "intermediate"
    if "прод" in v:
        return "advanced"
    return "beginner"


def _heuristic_extract(messages: List[ChatMsg], topic_hint: str) -> tuple[str, List[str]]:
    """
    Простой извлекатель целей/ошибок без LLM:
      - goals = topic_hint (если задан) или краткое резюме из последнего user-сообщения;
      - errors — ищем фразы "не понимаю/ошибка/путаю/трудно/сложно/проблема".
    """
    text_all = "\n".join(m.content for m in messages if m and m.content)
    user_texts = [m.content for m in messages if m.role == "user"]
    last_user = user_texts[-1] if user_texts else ""

    goals = (topic_hint or "").strip()
    if not goals:
        # берём первые 80 символов последнего вопроса пользователя как "цель/тему"
        goals = re.sub(r"\s+", " ", last_user).strip()[:80] or "общая тема"

    # вытягиваем "ошибки" по ключевым словам
    err_keys = ["не понимаю", "не получается", "ошибка", "путаю", "трудно", "сложно", "проблем", "косяк"]
    errors = []
    for k in err_keys:
        if k in text_all.lower():
            errors.append(k)
    # убираем дубли и ограничим разумно
    errors = list(dict.fromkeys(errors))[:6]
    return goals, errors


def _llm_extract(messages: List[ChatMsg], topic_hint: str) -> Optional[tuple[str, List[str]]]:
    """
    Пытаемся извлечь goals/errors через LLM (строгий JSON). При любой ошибке → None.
    """
    if not _LLM_OK or not _client:
        return None
    model = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")

    system = (
        "Ты помощник-экстрактор. Верни только JSON вида:\n"
        "{\"goals\":\"...\",\"errors\":[\"...\"]}\n"
        "Без пояснений."
    )
    user = {
        "topic_hint": topic_hint,
        "messages": [{"role": m.role, "content": m.content} for m in messages][-30:],
    }

    try:
        resp = _client.chat.completions.create(
            model=model,
            temperature=0.0,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
        )
        text = resp.choices[0].message.content or "{}"
        data = json.loads(text)
        goals = (data.get("goals") or topic_hint or "").strip()
        errors = [e for e in (data.get("errors") or []) if str(e).strip()]
        return goals or "общая тема", errors[:8]
    except (RateLimitError, AuthenticationError, APIConnectionError, APIStatusError) as e:
        print(f"[agents.llm_extract] API error: {e}")
        return None
    except Exception as e:
        print(f"[agents.llm_extract] parse error: {e}")
        return None


def _sanitize_questions(questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, q in enumerate(questions or []):
        text = str(q.get("text") or "").strip() or f"(fallback) Вопрос {i+1}"
        opts = q.get("options") or []
        if not isinstance(opts, list):
            opts = []
        opts = [str(x).strip() for x in opts if str(x).strip()][:4]
        while len(opts) < 4:
            opts.append(f"Вариант {len(opts)+1}")
        ans = q.get("answer")
        try:
            ans = int(ans) if ans is not None else 0
        except Exception:
            ans = 0
        if not (0 <= ans < 4):
            ans = 0
        out.append({"id": q.get("id") or f"q{i+1}", "text": text, "options": opts, "answer": ans})
    return out


# ====== РОУТЫ ======

@router.post("/curator/from_chat", response_model=CuratorFromChatResponse)
async def curator_from_chat(req: CuratorFromChatRequest):
    """
    1) Извлекаем цели/ошибки из переписки (LLM → эвристика).
    2) Вызываем Куратора (он подтянет память, применит LLM/эвристику и сохранит срез).
    3) Вызываем оркестратора, который строит план и при необходимости дергает под-агентов.
    4) (Опционально) дополнительно генерим экзамен прямо сейчас, если make_exam=True.
    """
    # шаг 1: goals/errors
    extracted = _llm_extract(req.messages, req.topic) or _heuristic_extract(req.messages, req.topic)
    goals, errors = extracted

    # шаг 2: оцениваем знания
    profile = await curator.assess_student(
        goals=goals,
        errors=errors,
        level=req.level,
        student_id=req.student_id,
    )

    # профайл для оркестратора — добавляем goals внутрь
    profile_for_orchestrator = dict(profile)
    profile_for_orchestrator.setdefault("goals", [goals] if goals else [])

    # готовим сырые сообщения для оркестратора
    chat_messages_for_orchestrator = [
        {"role": m.role, "content": m.content}
        for m in req.messages[-30:]  # последние 30 сообщений диалога
    ]

    # шаг 3: строим план и дергаем под-агентов
    orchestrator_block = None
    try:
        orchestrator_block = await orchestrator.plan_and_execute(
            student_id=req.student_id,
            profile=profile_for_orchestrator,
            chat_messages=chat_messages_for_orchestrator,
        )
    except Exception as e:
        # не ломаем основной ответ из-за ошибок оркестратора
        print(f"[agents.curator_from_chat] orchestrator failed: {e}")
        orchestrator_block = None

    resp: Dict[str, Any] = {
        "ok": True,
        "topic": req.topic or goals,
        "goals": goals,
        "errors": errors,
        "profile": profile,
        "orchestrator": orchestrator_block,
    }

    # шаг 4: при необходимости сразу делаем экзамен и возвращаем его в ответе
    if req.make_exam:
        data = examiner.generate_exam(count=max(1, min(20, req.count)), student_id=req.student_id)
        data["questions"] = _sanitize_questions(data.get("questions", []))
        resp["exam"] = data

    return resp



@router.post("/examiner", response_model=ExaminerResp)
async def examiner_route(req: ExaminerReq):
    """
    Генерация персональных тестов по последнему "срезу" Куратора.
    Гарантирует заполненные поля (text/options/answer).

    Если оркестратор заранее подготовил экзамен для данного student_id,
    то сначала пытаемся отдать его (и только если его нет — генерируем новый).
    """
    # сначала пробуем взять предгенерированный экзамен
    prepared = None
    try:
        prepared = examiner.pop_prepared_exam(req.student_id)  # type: ignore[attr-defined]
    except Exception as e:
        print(f"[agents.examiner_route] pop_prepared_exam failed: {e}")
        prepared = None

    if prepared is not None:
        data = prepared
    else:
        data = examiner.generate_exam(count=max(1, min(20, req.count)), student_id=req.student_id)

    questions = _sanitize_questions(data.get("questions", []))
    return {
        "ok": True,
        "questions": questions,
        "rubric": data.get("rubric", "1 балл за верный ответ."),
    }

class AfterExamRequest(BaseModel):
    student_id: str = "default"
    level: Literal["beginner", "intermediate", "advanced"] = "beginner"
    topic: str = ""
    ok: int          # сколько правильных
    total: int       # сколько всего


class AfterExamResponse(BaseModel):
    ok: bool
    orchestrator: OrchestratorBlock


@router.post("/after_exam", response_model=AfterExamResponse)
async def after_exam(req: AfterExamRequest):
    """
    Вызывается после прохождения теста.
    По результату теста обновляем профиль и снова дергаем Orchestrator,
    чтобы он решил, что делать дальше: материалы, новый тест или куратор.
    """
    # очень простой мэппинг результата → "ошибки" для куратора
    ratio = req.ok / max(1, req.total)
    errors: List[str] = []

    if ratio < 0.5:
        errors.append("плохо справился с тестом по теме")
    elif ratio < 0.8:
        errors.append("остались заметные пробелы по теме")
    else:
        errors.append("в целом хорошо справился с тестом")

    goals = req.topic or "закрепить текущую тему"

    # обновляем профиль через Куратора (он сам использует память)
    profile = await curator.assess_student(
        goals=goals,
        errors=errors,
        level=req.level,
        student_id=req.student_id,
    )

    # снова запускаем оркестратор: пусть он решает, что дальше
    orch_block = await orchestrator.plan_and_execute(
        student_id=req.student_id,
        profile=profile,
    )

    return AfterExamResponse(ok=True, orchestrator=orch_block)


# ====== ТВОЙ АГЕНТ: МАТЕРИАЛЫ ======
class MaterialsRequest(BaseModel):
    student_id: str = "default"


@router.post("/materials/generate")
def generate_materials(req: MaterialsRequest):
    """Генерирует персонализированные материалы для студента."""
    materials = materials_agent.generate_and_save_materials(req.student_id)
    return {"ok": True, "materials": materials}


@router.get("/materials")
def get_materials(student_id: str = "default"):
    """Возвращает материалы для студента."""
    return materials_agent.get_materials_for_student(student_id)