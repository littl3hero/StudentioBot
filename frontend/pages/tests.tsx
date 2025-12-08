// frontend/pages/tests.tsx
import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import { examinerGenerate } from '../lib/api';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:10000';

type Question = {
    id: string;
    text: string;
    options: string[];
    answer?: number;
    solution?: string;
    difficulty?: 'easy' | 'medium' | 'hard';
};

type Score = { ok: number; total: number } | null;

type AfterExamResponse = {
    ok: boolean;
    orchestrator?: {
        instruction_message: string;
        auto_route?: string | null;
        // остальные поля нам здесь не критичны
    };
};

function normalizeMathDelimiters(content: string): string {
    return content
        .replace(/\\\(/g, '$')
        .replace(/\\\)/g, '$')
        .replace(/\\\[/g, '$$')
        .replace(/\\\]/g, '$$');
}

export default function TestsPage() {
    const [count, setCount] = useState(5);
    const [loading, setLoading] = useState(false);
    const [questions, setQuestions] = useState<Question[]>([]);
    const [rubric, setRubric] = useState('');
    const [answers, setAnswers] = useState<Record<string, number>>({});
    const [score, setScore] = useState<Score>(null);
    const [nextLoading, setNextLoading] = useState(false);
    const [nextError, setNextError] = useState<string | null>(null);

    const router = useRouter();

    useEffect(() => {
        let initialCount = 5;

        try {
            const raw = localStorage.getItem('studentio_profile');
            if (raw) {
                const p = JSON.parse(raw);
                if (p?.level === 'advanced') {
                    initialCount = 8;
                }
            }
        } catch {
            // если что-то пошло не так — пусть будет 5
        }

        setCount(initialCount);

        // СРАЗУ тянем экзамен
        generate();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    async function generate() {
        setLoading(true);
        setQuestions([]);
        setRubric('');
        setAnswers({});
        setScore(null);
        setNextError(null);

        try {
            // 1) достаем student_id из того же профиля, что заполняет Куратор
            let student_id: string | null = null;
            try {
                const raw = localStorage.getItem('studentio_profile');
                if (raw) {
                    const p = JSON.parse(raw);
                    if (p?.student_id) {
                        student_id = p.student_id;
                    }
                }
            } catch {
                // игнорируем, student_id останется null
            }

            if (!student_id) {
                // нет профиля → просим вернуться к Куратору
                setNextError(
                    'Не найден Student ID. Сначала пообщайся с Куратором и нажми «Оценить по теме».'
                );
                return;
            }

            // 2) выстрел на бэк
            // backend сам: если есть prepared_exam → вернет его,
            // иначе сгенерит новый
            const data = await examinerGenerate(count, student_id);

            const qs: Question[] = data?.questions || [];
            setQuestions(qs);

            // реальное количество берем из ответа
            if (qs.length > 0) {
                setCount(qs.length);
            }

            setRubric(data?.rubric || '');
        } catch (e) {
            console.error(e);
            setQuestions([
                {
                    id: 'q1',
                    text: '(fallback) Тренировочный вопрос',
                    options: ['Ответ 1', 'Ответ 2', 'Ответ 3', 'Ответ 4'],
                },
            ]);
            setRubric('');
        } finally {
            setLoading(false);
        }
    }

    function mark(qid: string, optIdx: number) {
        setAnswers((prev) => ({ ...prev, [qid]: optIdx }));
    }

    function check() {
        let ok = 0;
        for (const q of questions) {
            if (typeof q.answer === 'number' && answers[q.id] === q.answer)
                ok++;
        }
        setScore({ ok, total: questions.length });
        setNextError(null);
    }

    async function nextStepAfterExam() {
        if (!score) return;
        setNextLoading(true);
        setNextError(null);

        try {
            // достаём профиль из localStorage (как на главной)
            let student_id = 'default';
            let level = 'beginner';
            let topic = '';

            try {
                const raw = localStorage.getItem('studentio_profile');
                if (raw) {
                    const p = JSON.parse(raw);
                    if (p?.student_id) student_id = p.student_id;
                    if (p?.level) level = p.level;
                    if (p?.last_topic) topic = p.last_topic;
                }
            } catch {
                // если профиль не прочитался — оставляем дефолты
            }

            const res = await fetch(`${API_BASE}/v1/agents/after_exam`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    student_id,
                    level,
                    topic,
                    ok: score.ok,
                    total: score.total,
                }),
            });

            if (!res.ok) {
                throw new Error(`after_exam failed: ${res.status}`);
            }

            const data: AfterExamResponse = await res.json();
            const autoRoute = data?.orchestrator?.auto_route;

            // можно, если захочешь, показать подсказку из instruction_message
            // но минимально — просто перейти по маршруту
            if (autoRoute) {
                router.push(autoRoute);
            } else {
                // если оркестратор не предложил маршрут — вернёмся в чат
                router.push('/');
            }
        } catch (e: any) {
            console.error(e);
            setNextError(
                e?.message || 'Не удалось получить следующий шаг от бота'
            );
        } finally {
            setNextLoading(false);
        }
    }

    return (
        <div className="mx-auto max-w-4xl px-4 py-6 space-y-6">
            <header className="flex items-center justify-between">
                <h1 className="text-2xl font-semibold">
                    Персональные тесты (Экзаменатор)
                </h1>
                <div className="text-sm text-white/60">
                    генерация по слабым местам
                </div>
            </header>

            <div className="card p-5 space-y-3">
                <div className="flex items-center gap-3">
                    <label className="text-sm">Количество вопросов</label>
                    <input
                        type="number"
                        min={1}
                        max={20}
                        value={count}
                        onChange={(e) =>
                            setCount(parseInt(e.target.value || '1'))
                        }
                        className="rounded-xl px-3 py-2 bg-white/10 outline-none focus:ring-2 focus:ring-white/15"
                    />
                    <button
                        onClick={generate}
                        disabled={loading}
                        className="rounded-xl px-4 py-2 bg-white/15 border border-white/10 hover:bg-white/20 disabled:opacity-50"
                    >
                        {loading ? 'Генерация…' : 'Сгенерировать тест'}
                    </button>
                </div>
                {rubric ? (
                    <div className="text-sm text-white/70">
                        Критерии: {rubric}
                    </div>
                ) : null}
            </div>

            {questions.map((q, idx) => (
                <div key={q.id} className="card p-5 space-y-2">
                    <div className="font-medium prose prose-invert max-w-none">
                        {idx + 1}.{' '}
                        <ReactMarkdown
                            remarkPlugins={[remarkGfm, remarkMath]}
                            rehypePlugins={[rehypeKatex]}
                        >
                            {normalizeMathDelimiters(q.text)}
                        </ReactMarkdown>
                    </div>

                    <div className="text-xs text-white/50">
                        {q.difficulty ? `Сложность: ${q.difficulty}` : ''}
                    </div>
                    <div className="grid gap-2 mt-2">
                        {q.options.map((opt, i) => (
                            <label
                                key={i}
                                className="inline-flex items-center gap-2"
                            >
                                <input
                                    type="radio"
                                    name={q.id}
                                    className="accent-white/80"
                                    checked={answers[q.id] === i}
                                    onChange={() => mark(q.id, i)}
                                />
                                <span className="text-sm">
                                    <ReactMarkdown
                                        remarkPlugins={[remarkGfm, remarkMath]}
                                        rehypePlugins={[rehypeKatex]}
                                    >
                                        {normalizeMathDelimiters(opt)}
                                    </ReactMarkdown>
                                </span>
                            </label>
                        ))}
                    </div>
                    {typeof q.answer === 'number' && score && (
                        <div className="text-sm text-white/70 space-y-1">
                            <div className="flex gap-1">
                                <span>Правильный:</span>
                                <ReactMarkdown
                                    remarkPlugins={[remarkGfm, remarkMath]}
                                    rehypePlugins={[rehypeKatex]}
                                >
                                    {normalizeMathDelimiters(
                                        q.options[q.answer]
                                    )}
                                </ReactMarkdown>
                            </div>

                            {q.solution && (
                                <div className="flex gap-1">
                                    <span>Разбор:</span>
                                    <ReactMarkdown
                                        remarkPlugins={[remarkGfm, remarkMath]}
                                        rehypePlugins={[rehypeKatex]}
                                    >
                                        {normalizeMathDelimiters(q.solution)}
                                    </ReactMarkdown>
                                </div>
                            )}
                        </div>
                    )}
                </div>
            ))}

            {questions.length > 0 && (
                <div className="flex flex-col gap-3">
                    <div className="flex items-center gap-6">
                        <button
                            onClick={check}
                            className="rounded-xl px-4 py-2 bg-white/15 border border-white/10 hover:bg-white/20"
                        >
                            Проверить
                        </button>
                        {score && (
                            <div className="text-white/80">
                                Результат: {score.ok} / {score.total}
                            </div>
                        )}
                    </div>

                    {score && (
                        <div className="flex items-center gap-4">
                            <button
                                onClick={nextStepAfterExam}
                                disabled={nextLoading}
                                className="rounded-xl px-4 py-2 bg-white/15 border border-white/10 hover:bg-white/20 disabled:opacity-50"
                            >
                                {nextLoading
                                    ? 'Определяю следующий шаг…'
                                    : 'Следующий шаг от бота'}
                            </button>
                            {nextError && (
                                <div className="text-sm text-red-300">
                                    {nextError}
                                </div>
                            )}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
