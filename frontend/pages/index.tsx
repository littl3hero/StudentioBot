// pages/index.tsx
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/router';

/** ===== Конфиг API (без новых файлов/прокси) =====
 * Укажи во фронтовом .env.local:
 *   NEXT_PUBLIC_API_BASE=https://<твой-backend>.onrender.com
 * Локально можно: http://localhost:10000
 */
const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:10000';
const CHAT_ENDPOINT = `${API_BASE}/v1/chat/stream`; // SSE чат прямо на backend
const CURATOR_FROM_CHAT_ENDPOINT = `${API_BASE}/v1/agents/curator/from_chat`; // оценка от Куратора

type Role = 'system' | 'user' | 'assistant';

type ChatMsg = {
    role: Role;
    content: string;
};

type CuratorFromChatRequest = {
    student_id: string;
    level: 'beginner' | 'intermediate' | 'advanced';
    topic: string;
    messages: ChatMsg[];
    make_exam?: boolean;
    count?: number;
};

type CuratorFromChatResponse = {
    ok: boolean;
    topic: string;
    goals: string;
    errors: string[];
    profile: {
        level: string;
        strengths?: string[];
        weaknesses?: string[];
        topics?: string[];
        notes?: string;
    };
    exam?: any;
};

/** ===== Вспомогательные функции для SSE ===== */
function parseSSELines(chunk: string): string[] {
    const lines: string[] = [];
    let start = 0;
    while (true) {
        const idx = chunk.indexOf('\n\n', start);
        if (idx === -1) break;
        lines.push(chunk.slice(start, idx));
        start = idx + 2;
    }
    return lines;
}

async function* ssePost(url: string, body: any): AsyncGenerator<string> {
    const res = await fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            Accept: 'text/event-stream',
        },
        body: JSON.stringify(body),
    });
    if (!res.ok || !res.body) {
        throw new Error(`SSE request failed: ${res.status} ${res.statusText}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = parseSSELines(buffer);
        const lastDoubleNL = buffer.lastIndexOf('\n\n');
        if (lastDoubleNL >= 0) buffer = buffer.slice(lastDoubleNL + 2);

        for (const line of lines) {
            if (line.startsWith('data: ')) {
                const payload = line.slice(6);
                if (payload === '[DONE]') return;
                try {
                    const obj = JSON.parse(payload);
                    if (obj.delta) yield obj.delta as string;
                } catch {
                    // игнорируем heartbeat/комменты
                }
            }
        }
    }
}

/** Разбудить Render перед запросом (на бесплатном тарифе он «засыпает») */
async function wakeBackend() {
    try {
        await fetch(`${API_BASE}/health`, { cache: 'no-store' });
    } catch {
        // игнор
    }
}

/** ===== UI главной страницы ===== */
export default function HomePage() {
    const router = useRouter();

    const [studentId, setStudentId] = useState('default');
    const [level, setLevel] = useState<
        'beginner' | 'intermediate' | 'advanced'
    >('beginner');
    const [topic, setTopic] = useState('');

    const [input, setInput] = useState('');
    const [sending, setSending] = useState(false);
    const [evaluating, setEvaluating] = useState(false);

    const [messages, setMessages] = useState<ChatMsg[]>([
        {
            role: 'system',
            content:
                'Ты — Учебный Куратор. Веди диалог, чтобы понять уровень ученика по выбранной теме и его типичные ошибки. Говори кратко и по делу.',
        },
        {
            role: 'assistant',
            content:
                'Привет! Напиши, по какой теме хочешь провериться и что именно вызывает сложности. Я помогу и задам пару уточняющих вопросов.',
        },
    ]);

    // автоскролл чата
    const logRef = useRef<HTMLDivElement>(null);
    useEffect(() => {
        logRef.current?.scrollTo({
            top: logRef.current.scrollHeight,
            behavior: 'smooth',
        });
    }, [messages]);

    const canSend = useMemo(
        () => input.trim().length > 0 && !sending && !evaluating,
        [input, sending, evaluating]
    );

    const handleSend = useCallback(async () => {
        const text = input.trim();
        if (!text) return;
        setInput('');
        setSending(true);

        setMessages((prev) => [...prev, { role: 'user', content: text }]);

        const userMsg = { role: 'user' as const, content: text };

        const topicContext: ChatMsg = {
            role: 'system',
            content: `Контекст для Куратора: текущая тема = "${
                topic || 'не выбрана'
            }". Отвечай по этой теме.`,
        };

        const history = [...messages, topicContext, userMsg].slice(-20);

        const assistantIndex = history.length;
        setMessages((prev) => [...prev, { role: 'assistant', content: '' }]);

        try {
            // будим backend и запускаем стрим
            await wakeBackend();
            for await (const delta of ssePost(CHAT_ENDPOINT, {
                messages: history,
            })) {
                setMessages((prev) => {
                    const copy = [...prev];
                    const last = copy[assistantIndex] || {
                        role: 'assistant',
                        content: '',
                    };
                    last.content = (last.content || '') + delta;
                    copy[assistantIndex] = last;
                    return copy;
                });
            }
        } catch (e) {
            console.error(e);
            setMessages((prev) => {
                const copy = [...prev];
                const last = copy[assistantIndex] || {
                    role: 'assistant',
                    content: '',
                };
                last.content =
                    (last.content || '') +
                    '\n\n[Ошибка сети при получении ответа. Проверь подключение к API (HTTPS) и CORS на backend.]';
                copy[assistantIndex] = last;
                return copy;
            });
        } finally {
            setSending(false);
        }
    }, [input, messages]);

    const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            handleSend();
        }
    };

    /** Оценка знаний по конкретной теме: извлекаем goals/errors из диалога и сохраняем профиль */
    const handleEvaluateTopic = useCallback(async () => {
        if (!topic.trim()) {
            alert('Укажи тему, по которой оценивать знания.');
            return;
        }
        setEvaluating(true);
        try {
            const payload: CuratorFromChatRequest = {
                student_id: studentId || 'default',
                level,
                topic,
                messages,
                make_exam: false,
                count: 5,
            };

            await wakeBackend();
            const res = await fetch(CURATOR_FROM_CHAT_ENDPOINT, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!res.ok)
                throw new Error(`curator/from_chat failed: ${res.status}`);
            const data: CuratorFromChatResponse = await res.json();

            // Сохраним «срез профиля» в localStorage — его подберёт /tests
            localStorage.setItem(
                'studentio_profile',
                JSON.stringify({
                    student_id: payload.student_id,
                    level: data?.profile?.level || level,
                    goals: data?.goals || '',
                    topics: data?.profile?.topics?.length
                        ? data.profile.topics
                        : [topic],
                    weaknesses: data?.profile?.weaknesses || [],
                    last_topic: topic,
                })
            );

            // Сообщение для пользователя
            setMessages((prev) => [
                ...prev,
                {
                    role: 'assistant',
                    content:
                        `Готово! Я оценил твой уровень по теме «${topic}».\n` +
                        `Цель: ${data?.goals || '—'}\n` +
                        `Слабые места: ${
                            data?.errors?.join(', ') || 'не явные'
                        }\n` +
                        `Оценка уровня: ${data?.profile?.level || level}.\n` +
                        `Перейди на вкладку «Тесты» — там ждёт персональный тест.`,
                },
            ]);

            // Переход на /tests
            await new Promise((r) => setTimeout(r, 150));
            router.push('/tests');
        } catch (e) {
            console.error(e);
            alert(
                'Не удалось провести оценку. Проверь NEXT_PUBLIC_API_BASE, CORS (ALLOW_ORIGINS) и доступность backend /health.'
            );
        } finally {
            setEvaluating(false);
        }
    }, [studentId, level, topic, messages, router]);

    return (
        <div className="mx-auto max-w-4xl px-4 py-6 space-y-6">
            <header className="flex items-center justify-between">
                <h1 className="text-2xl font-semibold">
                    Учебный помощник — Куратор
                </h1>
                <div className="text-sm text-white/60">
                    Чат → Оценка по теме → Тесты
                </div>
            </header>

            {/* Панель параметров */}
            <div className="grid gap-3 sm:grid-cols-4">
                <input
                    className="rounded-xl bg-white/10 px-3 py-2 outline-none focus:ring-2 focus:ring-white/15"
                    placeholder="Student ID"
                    value={studentId}
                    onChange={(e) => setStudentId(e.target.value)}
                />
                <select
                    className="rounded-xl bg-white/10 px-3 py-2 outline-none focus:ring-2 focus:ring-white/15"
                    value={level}
                    onChange={(e) =>
                        setLevel(
                            e.target.value as
                                | 'beginner'
                                | 'intermediate'
                                | 'advanced'
                        )
                    }
                >
                    <option value="beginner">Новичок</option>
                    <option value="intermediate">Средний</option>
                    <option value="advanced">Продвинутый</option>
                </select>
                <input
                    className="rounded-xl bg-white/10 px-3 py-2 outline-none focus:ring-2 focus:ring-white/15 sm:col-span-2"
                    placeholder="Тема (например, пределы и ε-δ)"
                    value={topic}
                    onChange={(e) => setTopic(e.target.value)}
                />
            </div>

            {/* Окно чата */}
            <div
                ref={logRef}
                className="rounded-2xl border border-white/10 bg-white/5 p-4 h-[52vh] overflow-y-auto space-y-4"
            >
                {messages.map((m, i) => (
                    <div key={i} className="flex gap-3">
                        <div
                            className={`h-6 w-6 flex items-center justify-center rounded-full text-xs ${
                                m.role === 'user'
                                    ? 'bg-emerald-500/20'
                                    : m.role === 'assistant'
                                    ? 'bg-sky-500/20'
                                    : 'bg-white/10'
                            }`}
                            title={m.role}
                        >
                            {m.role === 'user'
                                ? 'U'
                                : m.role === 'assistant'
                                ? 'A'
                                : 'S'}
                        </div>
                        <div className="whitespace-pre-wrap leading-relaxed">
                            {m.content}
                        </div>
                    </div>
                ))}
            </div>

            {/* Ввод и кнопки */}
            <div className="flex flex-col sm:flex-row gap-3">
                <input
                    className="flex-1 rounded-xl bg-white/10 px-4 py-3 outline-none focus:ring-2 focus:ring-white/15"
                    placeholder="Напиши сообщение... (Ctrl/⌘+Enter — отправить)"
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={handleKeyDown}
                />
                <div className="flex gap-3">
                    <button
                        onClick={handleSend}
                        disabled={!canSend}
                        className="rounded-xl px-4 py-3 bg-white/15 border border-white/10 hover:bg-white/20 disabled:opacity-50"
                        title="Отправить (Ctrl/⌘+Enter)"
                    >
                        Отправить
                    </button>
                    <button
                        onClick={handleEvaluateTopic}
                        disabled={evaluating || sending}
                        className="rounded-xl px-4 py-3 bg-white/15 border border-white/10 hover:bg-white/20 disabled:opacity-50"
                        title="Оценить знания по теме и перейти к тестам"
                    >
                        {evaluating ? 'Оцениваем…' : 'Оценить по теме → Тесты'}
                    </button>
                </div>
            </div>

            <footer className="text-xs text-white/50">
                Подсказка: сначала пообщайся с Куратором по выбранной теме,
                затем нажми «Оценить по теме». Экзаменатор на странице «Тесты»
                использует сохранённый профиль.
            </footer>
        </div>
    );
}
