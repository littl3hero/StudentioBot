import { useEffect, useRef, useState } from 'react';
import { API_BASE } from '../lib/api';

type Role = 'user' | 'assistant' | 'system';
type Msg = { role: Role; content: string };

export default function Home() {
    const [input, setInput] = useState('');
    const [messages, setMessages] = useState<Msg[]>([
        { role: 'system', content: 'Ты — лаконичный помощник.' },
    ]);
    const [streaming, setStreaming] = useState(false);
    const outRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        outRef.current?.scrollTo({
            top: outRef.current.scrollHeight,
            behavior: 'smooth',
        });
    }, [messages, streaming]);

    async function send() {
        if (!input.trim() || streaming) return;
        const next: Msg[] = [
            ...messages,
            { role: 'user' as Role, content: input },
        ];
        setMessages(next);
        setInput('');
        setStreaming(true);

        const body = JSON.stringify({
            messages: next.map((m) => ({ role: m.role, content: m.content })),
            model: 'gpt-4o-mini',
            temperature: 0.7,
        });

        // Используем EventSource-подобный парсинг вручную (SSE через fetch)
        const res = await fetch(`${API_BASE}/v1/chat/stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body,
        });

        if (!res.ok || !res.body) {
            setStreaming(false);
            setMessages((m) => [
                ...m,
                { role: 'assistant', content: 'Ошибка сервера' },
            ]);
            return;
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let assistant = '';

        // предварительно добавим пустое ассистент-сообщение
        setMessages((m) => [...m, { role: 'assistant', content: '' }]);

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            const chunk = decoder.decode(value, { stream: true });

            // SSE: строки вида "data: {...}\n\n"
            const lines = chunk.split('\n');
            for (const line of lines) {
                if (!line.startsWith('data:')) continue;
                const payload = line.slice(5).trim();
                if (payload === '[DONE]') {
                    setStreaming(false);
                    return;
                }
                try {
                    const json = JSON.parse(payload);
                    if (json.error) {
                        assistant += `\n[error] ${json.error}`;
                    } else if (json.delta) {
                        assistant += json.delta;
                    }
                    // обновляем последнее ассистент-сообщение
                    setMessages((curr) => {
                        const copy = [...curr];
                        const lastIdx = copy.length - 1;
                        if (copy[lastIdx]?.role === 'assistant') {
                            copy[lastIdx] = {
                                role: 'assistant',
                                content: assistant,
                            };
                        }
                        return copy;
                    });
                } catch {
                    // игнорируем шум
                }
            }
        }
        setStreaming(false);
    }

    return (
        <main style={{ maxWidth: 800, margin: '24px auto', padding: 16 }}>
            <h1>AI Chat (Streaming)</h1>
            <div
                ref={outRef}
                style={{
                    height: 420,
                    overflowY: 'auto',
                    border: '1px solid #eee',
                    borderRadius: 8,
                    padding: 12,
                    marginBottom: 12,
                }}
            >
                {messages
                    .filter((m) => m.role !== 'system')
                    .map((m, i) => (
                        <div key={i} style={{ margin: '8px 0' }}>
                            <b>{m.role === 'user' ? 'Вы' : 'AI'}:</b>{' '}
                            {m.content}
                        </div>
                    ))}
                {streaming && <div style={{ opacity: 0.6 }}>…генерация</div>}
            </div>

            <div style={{ display: 'flex', gap: 8 }}>
                <input
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    placeholder="Напишите сообщение"
                    style={{
                        flex: 1,
                        padding: 10,
                        borderRadius: 8,
                        border: '1px solid #ddd',
                    }}
                />
                <button
                    onClick={send}
                    disabled={streaming}
                    style={{ padding: '10px 16px' }}
                >
                    Отправить
                </button>
            </div>
        </main>
    );
}
