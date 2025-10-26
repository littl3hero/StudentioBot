export const API_BASE = process.env.NEXT_PUBLIC_API_BASE || '';

type FetchOptions = {
    method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
    body?: any;
    headers?: Record<string, string>;
};

async function request<T>(path: string, opts: FetchOptions = {}): Promise<T> {
    if (!API_BASE) throw new Error('API_BASE is not configured');
    const res = await fetch(`${API_BASE}${path}`, {
        method: opts.method || 'GET',
        headers: {
            'Content-Type': 'application/json',
            ...(opts.headers || {}),
        },
        body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (!res.ok) {
        const text = await res.text();
        throw new Error(`HTTP ${res.status}: ${text}`);
    }
    return res.json();
}

export type StudentProfile = {
    name: string;
    goals: string;
    level: string;
    notes: string;
};

export async function saveStudentProfile(profile: StudentProfile) {
    return request<{ ok: boolean; id?: string }>('/student', {
        method: 'POST',
        body: profile,
    });
}

export async function getStudentProfile() {
    return request<StudentProfile>('/student');
}

export async function generateQuiz(topic: string, level: string) {
    return request<{
        questions: {
            id: string;
            text: string;
            options: string[];
            answer?: number;
        }[];
    }>('/tests/generate', { method: 'POST', body: { topic, level } });
}
