import { useEffect, useState } from 'react';
import { generateMaterials, listMaterials, Material } from '../lib/api';

type StoredProfile = {
    student_id?: string;
    level?: string;
    goals?: string;
    topics?: string[];
    weaknesses?: string[];
    last_topic?: string;
};

export default function MaterialsPage() {
    const [studentId, setStudentId] = useState('default');
    const [profile, setProfile] = useState<StoredProfile | null>(null);

    const [materials, setMaterials] = useState<Material[]>([]);
    const [initialLoading, setInitialLoading] = useState(true);
    const [generating, setGenerating] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // 1) Забираем профиль из localStorage, чтобы знать student_id и последнюю тему
    useEffect(() => {
        try {
            const raw = localStorage.getItem('studentio_profile');
            if (raw) {
                const p: StoredProfile = JSON.parse(raw);
                if (p?.student_id) {
                    setStudentId(p.student_id);
                }
                setProfile(p);
            }
        } catch (e) {
            console.warn('Failed to parse studentio_profile', e);
        }
    }, []);

    // 2) При изменении studentId подгружаем материалы из backend
    useEffect(() => {
        async function load() {
            setInitialLoading(true);
            setError(null);
            try {
                const data = await listMaterials(studentId);
                setMaterials(data || []);
            } catch (e) {
                console.error(e);
                setError(
                    'Не удалось загрузить материалы. Попробуй сгенерировать новые.'
                );
            } finally {
                setInitialLoading(false);
            }
        }

        load();
    }, [studentId]);

    async function handleGenerate() {
        setGenerating(true);
        setError(null);
        try {
            // 1) сгенерили и сохранили в БД
            await generateMaterials(studentId);
            // 2) вытянули ВСЕ материалы этого студента
            const all = await listMaterials(studentId);
            setMaterials(all);
        } catch (e) {
            console.error(e);
            setError('Ошибка при генерации материалов. Попробуй ещё раз.');
        } finally {
            setGenerating(false);
        }
    }

    const hasMaterials = materials && materials.length > 0;

    return (
        <div className="space-y-6">
            <header className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                <div>
                    <h1 className="text-2xl font-semibold">Материалы</h1>
                    <p className="text-sm text-white/60">
                        Конспекты, шпаргалки и ссылки, сгенерированные под твой
                        профиль.
                    </p>
                </div>
                <div className="text-xs text-white/50">
                    Student ID:{' '}
                    <span className="font-mono bg-white/5 px-2 py-1 rounded-lg">
                        {studentId || 'default'}
                    </span>
                </div>
            </header>

            {/* Инфо о профиле, которую положил Куратор */}
            {profile && (
                <div className="card p-4 text-sm text-white/70 space-y-1">
                    <div>
                        <span className="text-white/50">Уровень:</span>{' '}
                        <span className="font-medium">
                            {profile.level || 'не указан'}
                        </span>
                    </div>
                    <div>
                        <span className="text-white/50">Последняя тема:</span>{' '}
                        <span className="font-medium">
                            {profile.last_topic || profile.topics?.[0] || '—'}
                        </span>
                    </div>
                    {profile.weaknesses && profile.weaknesses.length > 0 && (
                        <div>
                            <span className="text-white/50">Слабые места:</span>{' '}
                            <span>{profile.weaknesses.join(', ')}</span>
                        </div>
                    )}
                </div>
            )}

            {/* Панель действий */}
            <div className="card p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
                <div className="text-sm text-white/70">
                    Нажми «Сгенерировать», чтобы агент подобрал материалы под
                    твои ошибки и цели.
                </div>
                <button
                    onClick={handleGenerate}
                    disabled={generating}
                    className="rounded-xl px-4 py-2 bg-white/15 border border-white/10 hover:bg-white/20 disabled:opacity-50"
                >
                    {generating ? 'Генерация…' : 'Сгенерировать материалы'}
                </button>
            </div>

            {error && (
                <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded-xl p-3">
                    {error}
                </div>
            )}

            {/* Список материалов */}
            <div className="space-y-3">
                {initialLoading && (
                    <div className="text-sm text-white/60">
                        Загрузка материалов…
                    </div>
                )}

                {!initialLoading && !hasMaterials && !error && (
                    <div className="card p-4 text-sm text-white/60">
                        Пока материалов нет. Сначала пообщайся с Куратором на
                        главной странице, а потом нажми «Сгенерировать
                        материалы».
                    </div>
                )}

                {hasMaterials &&
                    materials.map((m, i) => (
                        <div key={i} className="card p-4 space-y-2">
                            <div className="flex items-center justify-between gap-2">
                                <h2 className="font-semibold text-lg">
                                    {m.title}
                                </h2>
                                <span className="text-xs px-2 py-1 rounded-full bg-white/10 text-white/70 capitalize">
                                    {m.type === 'cheat_sheet'
                                        ? 'Шпаргалка'
                                        : m.type === 'notes'
                                        ? 'Конспект'
                                        : 'Ссылка'}
                                </span>
                            </div>

                            {m.url && (
                                <a
                                    href={m.url}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="text-sm text-sky-300 hover:text-sky-200 underline"
                                >
                                    Открыть ресурс
                                </a>
                            )}

                            {m.content && (
                                <p className="text-sm text-white/70 whitespace-pre-wrap">
                                    {m.content}
                                </p>
                            )}
                        </div>
                    ))}
            </div>
        </div>
    );
}
