import { useState } from 'react';
import { generateQuiz } from '../lib/api';

type Question = {
    id: string;
    text: string;
    options: string[];
    answer?: number;
};

export default function TestsPage() {
    const [topic, setTopic] = useState('math');
    const [level, setLevel] = useState('beginner');
    const [loading, setLoading] = useState(false);
    const [quiz, setQuiz] = useState<Question[]>([]);

    async function createQuiz() {
        setLoading(true);
        try {
            const data = await generateQuiz(topic, level);
            setQuiz(data.questions);
        } catch (e) {
            console.warn(e);
            setQuiz([
                {
                    id: 'q1',
                    text: 'Определение предела по Коши — это...?',
                    options: [
                        'про ε-δ',
                        'про ряды',
                        'про производные',
                        'про интегралы',
                    ],
                    answer: 0,
                },
                {
                    id: 'q2',
                    text: 'LIFO-структура данных — это...',
                    options: ['Очередь', 'Стек', 'Дерево', 'Граф'],
                    answer: 1,
                },
                {
                    id: 'q3',
                    text: 'Какой порядок у O(log n)?',
                    options: [
                        'Линейный',
                        'Константный',
                        'Логарифмический',
                        'Квадратичный',
                    ],
                    answer: 2,
                },
            ]);
        } finally {
            setLoading(false);
        }
    }

    return (
        <div className="space-y-6">
            <h1 className="text-2xl font-semibold">Тесты от Экзаменатора</h1>

            <div className="card p-6 space-y-4">
                <div className="grid gap-4 sm:grid-cols-3">
                    <label className="space-y-2">
                        <span className="text-sm text-white/70">Тема</span>
                        <select
                            className="w-full rounded-xl bg-bg/40 border border-white/10 px-3 py-2 outline-none focus:ring-2 focus:ring-white/10"
                            value={topic}
                            onChange={(e) => setTopic(e.target.value)}
                        >
                            <option value="math">Математика</option>
                            <option value="cs">CS / Алгоритмы</option>
                            <option value="logic">
                                Логика / Булевы функции
                            </option>
                        </select>
                    </label>
                    <label className="space-y-2">
                        <span className="text-sm text-white/70">Уровень</span>
                        <select
                            className="w-full rounded-xl bg-bg/40 border border-white/10 px-3 py-2 outline-none focus:ring-2 focus:ring-white/10"
                            value={level}
                            onChange={(e) => setLevel(e.target.value)}
                        >
                            <option value="beginner">Новичок</option>
                            <option value="intermediate">Средний</option>
                            <option value="advanced">Продвинутый</option>
                        </select>
                    </label>
                    <div className="flex items-end">
                        <button
                            onClick={createQuiz}
                            disabled={loading}
                            className="inline-flex items-center justify-center gap-2 rounded-xl bg-white/10 hover:bg-white/15 px-4 py-2 border border-white/10 transition w-full"
                        >
                            {loading ? 'Генерация…' : 'Сгенерировать'}
                        </button>
                    </div>
                </div>
            </div>

            {quiz.length > 0 && (
                <div className="space-y-4">
                    {quiz.map((q, qi) => (
                        <div key={q.id} className="card p-5">
                            <div className="flex items-start gap-3">
                                <div className="mt-1 text-white/60">
                                    {qi + 1}.
                                </div>
                                <div className="flex-1 space-y-3">
                                    <div className="font-medium">{q.text}</div>
                                    <div className="grid gap-2">
                                        {q.options.map((opt, i) => (
                                            <label
                                                key={i}
                                                className="inline-flex items-center gap-2"
                                            >
                                                <input
                                                    type="radio"
                                                    name={q.id}
                                                    className="accent-white/80"
                                                />
                                                <span className="text-white/80">
                                                    {opt}
                                                </span>
                                            </label>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        </div>
                    ))}
                    <button className="inline-flex items-center gap-2 rounded-xl bg-white/10 hover:bg-white/15 px-4 py-2 border border-white/10 transition">
                        Добавить вопрос
                    </button>
                </div>
            )}
        </div>
    );
}
