// frontend/pages/tests.tsx
import { useEffect, useState } from 'react';
import { examinerGenerate } from '../lib/api';

type Question = {
    id: string;
    text: string;
    options: string[];
    answer?: number;
    solution?: string;
    difficulty?: 'easy' | 'medium' | 'hard';
};

export default function TestsPage() {
    const [count, setCount] = useState(5);
    const [loading, setLoading] = useState(false);
    const [questions, setQuestions] = useState<Question[]>([]);
    const [rubric, setRubric] = useState('');
    const [answers, setAnswers] = useState<Record<string, number>>({});
    const [score, setScore] = useState<{ ok: number; total: number } | null>(
        null
    );

    useEffect(() => {
        // необязательно: подтянем «срез профиля» из localStorage,
        // чтобы, например, менять дефолтное число вопросов по уровню
        try {
            const raw = localStorage.getItem('studentio_profile');
            if (raw) {
                const p = JSON.parse(raw);
                if (p?.level === 'advanced') setCount(8);
            }
        } catch {}
    }, []);

    async function generate() {
        setLoading(true);
        setQuestions([]);
        setRubric('');
        setAnswers({});
        setScore(null);

        try {
            // вытащим student_id из того же профиля, что сохраняет куратор
            let student_id = 'default';
            try {
                const raw = localStorage.getItem('studentio_profile');
                if (raw) {
                    const p = JSON.parse(raw);
                    if (p?.student_id) {
                        student_id = p.student_id;
                    }
                }
            } catch {
                // если что-то пошло не так — используем 'default'
            }

            const data = await examinerGenerate(count, student_id);
            setQuestions((data?.questions || []).slice(0, count));
            setRubric(data?.rubric || '');
        } catch (e) {
            console.error(e);
            // fallback, чтобы UI не ломался
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
                    <div className="font-medium">
                        {idx + 1}. {q.text}
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
                                <span>{opt}</span>
                            </label>
                        ))}
                    </div>
                    {typeof q.answer === 'number' && score && (
                        <div className="text-sm text-white/70">
                            Правильный: {q.options[q.answer]}
                            {q.solution ? ` • Разбор: ${q.solution}` : ''}
                        </div>
                    )}
                </div>
            ))}

            {questions.length > 0 && (
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
            )}
        </div>
    );
}
