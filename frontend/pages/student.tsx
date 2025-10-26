import { useEffect, useState } from 'react';
import { saveStudentProfile, getStudentProfile } from '../lib/api';

export default function StudentPage() {
    const [profile, setProfile] = useState({
        name: '',
        goals: '',
        level: 'beginner',
        notes: '',
    });

    useEffect(() => {
        (async () => {
            try {
                const p = await getStudentProfile();
                setProfile(p);
            } catch (e) {
                console.warn('Cannot load profile:', e);
            }
        })();
    }, []);

    return (
        <div className="space-y-6">
            <h1 className="text-2xl font-semibold">Данные об ученике</h1>
            <div className="card p-6 space-y-6">
                <div className="grid gap-4 sm:grid-cols-2">
                    <label className="space-y-2">
                        <span className="text-sm text-white/70">Имя</span>
                        <input
                            className="w-full rounded-xl bg-bg/40 border border-white/10 px-3 py-2 outline-none focus:ring-2 focus:ring-white/10"
                            value={profile.name}
                            onChange={(e) =>
                                setProfile({ ...profile, name: e.target.value })
                            }
                            placeholder="Иван"
                        />
                    </label>
                    <label className="space-y-2">
                        <span className="text-sm text-white/70">Уровень</span>
                        <select
                            className="w-full rounded-xl bg-bg/40 border border-white/10 px-3 py-2 outline-none focus:ring-2 focus:ring-white/10"
                            value={profile.level}
                            onChange={(e) =>
                                setProfile({
                                    ...profile,
                                    level: e.target.value,
                                })
                            }
                        >
                            <option value="beginner">Новичок</option>
                            <option value="intermediate">Средний</option>
                            <option value="advanced">Продвинутый</option>
                        </select>
                    </label>
                    <label className="space-y-2 sm:col-span-2">
                        <span className="text-sm text-white/70">
                            Цели обучения
                        </span>
                        <textarea
                            rows={3}
                            className="w-full rounded-xl bg-bg/40 border border-white/10 px-3 py-2 outline-none focus:ring-2 focus:ring-white/10"
                            value={profile.goals}
                            onChange={(e) =>
                                setProfile({
                                    ...profile,
                                    goals: e.target.value,
                                })
                            }
                            placeholder="Понимать матанализ / сдать сессию / улучшить C/C++"
                        />
                    </label>
                    <label className="space-y-2 sm:col-span-2">
                        <span className="text-sm text-white/70">Заметки</span>
                        <textarea
                            rows={4}
                            className="w-full rounded-xl bg-bg/40 border border-white/10 px-3 py-2 outline-none focus:ring-2 focus:ring-white/10"
                            value={profile.notes}
                            onChange={(e) =>
                                setProfile({
                                    ...profile,
                                    notes: e.target.value,
                                })
                            }
                            placeholder="Особенности, предпочтения, дедлайны..."
                        />
                    </label>
                </div>

                <button
                    className="inline-flex items-center gap-2 rounded-xl bg-white/10 hover:bg-white/15 px-4 py-2 border border-white/10 transition"
                    onClick={async () => {
                        try {
                            await saveStudentProfile(profile);
                            alert('Сохранено');
                        } catch (e: any) {
                            console.warn(e);
                            alert('Не удалось сохранить: ' + e.message);
                        }
                    }}
                >
                    Сохранить
                </button>
            </div>
        </div>
    );
}
