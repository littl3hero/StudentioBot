// frontend/pages/student.tsx
import { useEffect, useState } from 'react';
import {
    getStudentProfile,
    saveStudentProfile,
    StudentProfile,
} from '../lib/api';

export default function StudentPage() {
    const [form, setForm] = useState<StudentProfile>({
        name: '',
        goals: '',
        level: 'beginner',
        notes: '',
    });
    const [saving, setSaving] = useState(false);
    const [loaded, setLoaded] = useState(false);
    const [okMsg, setOkMsg] = useState('');

    useEffect(() => {
        (async () => {
            try {
                const p = await getStudentProfile();
                setForm(p);
            } catch {
                // ok
            } finally {
                setLoaded(true);
            }
        })();
    }, []);

    async function onSave() {
        setSaving(true);
        setOkMsg('');
        try {
            await saveStudentProfile(form);
            setOkMsg('Профиль сохранён');
        } catch (e) {
            alert('Не удалось сохранить профиль');
        } finally {
            setSaving(false);
            setTimeout(() => setOkMsg(''), 2000);
        }
    }

    return (
        <div className="mx-auto max-w-3xl px-4 py-6 space-y-6">
            <h1 className="text-2xl font-semibold">Профиль студента</h1>

            {!loaded ? (
                <div>Загрузка…</div>
            ) : (
                <div className="card p-6 space-y-4">
                    <label className="space-y-1 block">
                        <div className="text-sm text-white/70">Имя / ID</div>
                        <input
                            className="w-full rounded-xl bg-white/10 px-3 py-2 outline-none focus:ring-2 focus:ring-white/15"
                            value={form.name}
                            onChange={(e) =>
                                setForm({ ...form, name: e.target.value })
                            }
                            placeholder="ibrahim"
                        />
                    </label>

                    <label className="space-y-1 block">
                        <div className="text-sm text-white/70">Цели</div>
                        <input
                            className="w-full rounded-xl bg-white/10 px-3 py-2 outline-none focus:ring-2 focus:ring-white/15"
                            value={form.goals}
                            onChange={(e) =>
                                setForm({ ...form, goals: e.target.value })
                            }
                            placeholder="подготовка к контрольной по матану"
                        />
                    </label>

                    <div className="grid gap-4 sm:grid-cols-2">
                        <label className="space-y-1 block">
                            <div className="text-sm text-white/70">Уровень</div>
                            <select
                                className="w-full rounded-xl bg-white/10 px-3 py-2 outline-none focus:ring-2 focus:ring-white/15"
                                value={form.level}
                                onChange={(e) =>
                                    setForm({
                                        ...form,
                                        level: e.target
                                            .value as StudentProfile['level'],
                                    })
                                }
                            >
                                <option value="beginner">Новичок</option>
                                <option value="intermediate">Средний</option>
                                <option value="advanced">Продвинутый</option>
                            </select>
                        </label>

                        <label className="space-y-1 block">
                            <div className="text-sm text-white/70">Заметки</div>
                            <input
                                className="w-full rounded-xl bg-white/10 px-3 py-2 outline-none focus:ring-2 focus:ring-white/15"
                                value={form.notes}
                                onChange={(e) =>
                                    setForm({ ...form, notes: e.target.value })
                                }
                                placeholder="не люблю ε-δ"
                            />
                        </label>
                    </div>

                    <div className="flex items-center gap-3">
                        <button
                            onClick={onSave}
                            disabled={saving}
                            className="rounded-xl px-4 py-2 bg-white/15 border border-white/10 hover:bg-white/20 disabled:opacity-50"
                        >
                            {saving ? 'Сохранение…' : 'Сохранить'}
                        </button>
                        {okMsg && (
                            <div className="text-sm text-emerald-400">
                                {okMsg}
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
