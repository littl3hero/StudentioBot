// frontend/lib/api.ts
export const API_BASE =
    process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:10000';

/* ---------- Student (legacy API совместимость) ---------- */

export type StudentProfile = {
    name: string;
    goals: string;
    level: string; // "beginner" | "intermediate" | "advanced"
    notes: string;
};

export async function getStudentProfile(): Promise<StudentProfile> {
    const r = await fetch(`${API_BASE}/student`, { cache: 'no-store' });
    if (!r.ok) throw new Error(`getStudentProfile failed: ${r.status}`);
    return r.json();
}

export async function saveStudentProfile(profile: StudentProfile) {
    const r = await fetch(`${API_BASE}/student`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(profile),
    });
    if (!r.ok) throw new Error(`saveStudentProfile failed: ${r.status}`);
    return r.json();
}

/* ---------- Legacy quiz (если ещё используешь /tests/generate) ---------- */

export async function generateQuiz(topic: string, level: string) {
    const r = await fetch(`${API_BASE}/tests/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic, level }),
    });
    if (!r.ok) throw new Error(`tests/generate failed: ${r.status}`);
    return r.json();
}

/* ---------- Multi-agent endpoints ---------- */

export type ChatMsg = {
    role: 'system' | 'user' | 'assistant';
    content: string;
};

export async function curatorFromChat(input: {
    student_id: string;
    level: 'beginner' | 'intermediate' | 'advanced';
    topic: string;
    messages: ChatMsg[];
    make_exam?: boolean;
    count?: number;
}) {
    const r = await fetch(`${API_BASE}/v1/agents/curator/from_chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
    });
    if (!r.ok) throw new Error(`curator/from_chat failed: ${r.status}`);
    return r.json(); // { ok, topic, goals, errors, profile, exam? }
}

export async function examinerGenerate(count: number) {
    const r = await fetch(`${API_BASE}/v1/agents/examiner`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ count }),
    });
    if (!r.ok) throw new Error(`examiner failed: ${r.status}`);
    return r.json(); // { ok, questions, rubric }
}

export async function sessionLearn(input: {
    student_id: string;
    goals: string;
    errors: string[];
    level: 'beginner' | 'intermediate' | 'advanced';
    count?: number;
}) {
    const r = await fetch(`${API_BASE}/v1/agents/session`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
    });
    if (!r.ok) throw new Error(`session learn failed: ${r.status}`);
    return r.json(); // { ok, profile, exam }
}

// ---------- Materials agent ----------

export type Material = {
    title: string;
    type: 'link' | 'notes' | 'cheat_sheet';
    url?: string | null;
    content?: string | null;
};

export async function generateMaterials(
    student_id: string = 'default'
): Promise<{ ok: boolean; materials: Material[] }> {
    const r = await fetch(`${API_BASE}/v1/agents/materials/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ student_id }),
    });
    if (!r.ok) throw new Error(`materials/generate failed: ${r.status}`);
    return r.json();
}

export async function listMaterials(
    student_id: string = 'default'
): Promise<Material[]> {
    const params = new URLSearchParams();
    if (student_id) params.set('student_id', student_id);

    const r = await fetch(
        `${API_BASE}/v1/agents/materials?${params.toString()}`,
        { cache: 'no-store' }
    );
    if (!r.ok) throw new Error(`materials get failed: ${r.status}`);
    return r.json();
}
