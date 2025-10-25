import OpenAI from "openai";

const OPENAI_API_KEY = process.env.OPENAI_API_KEY || "";
const OPENAI_MODEL = process.env.OPENAI_MODEL || "gpt-4o-mini";

// (опционально) OpenRouter
const OR_KEY = process.env.OPENROUTER_API_KEY || "";
const OR_MODEL = process.env.OPENROUTER_MODEL || "meta-llama/llama-3.1-8b-instruct";

export async function generateTip({ level, errors }) {
  const sys = [
    "Ты — Куратор-методист.",
    "Кратко и чётко помоги исправить типичные ошибки.",
    "Пиши по-русски, 4–6 маркеров (—), с мини-примерами, подстраивайся под уровень.",
  ].join(" ");

  const usr =
    `Уровень: ${level}\n` +
    `Ошибки: ${errors && errors.length ? errors.join(", ") : "не указаны"}\n` +
    `Сформируй компактную шпаргалку/советы (4–6 строк).`;

  // OpenAI
  if (OPENAI_API_KEY) {
    const client = new OpenAI({ apiKey: OPENAI_API_KEY });
    const resp = await client.chat.completions.create({
      model: OPENAI_MODEL,
      temperature: 0.6,
      messages: [
        { role: "system", content: sys },
        { role: "user", content: usr }
      ],
    });
    return resp.choices[0].message.content.trim();
  }

  // OpenRouter (fallback)
  if (OR_KEY) {
    const r = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${OR_KEY}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        model: OR_MODEL,
        messages: [
          { role: "system", content: sys },
          { role: "user", content: usr }
        ],
        temperature: 0.6
      })
    });
    if (!r.ok) throw new Error(`OpenRouter error: ${r.status}`);
    const data = await r.json();
    return (data.choices?.[0]?.message?.content || "").trim();
  }

  // Простейший offline-фоллбек (если ключей нет — чтобы MiniApp не падал)
  const templ = {
    beginner: [
      "— Переписывай условие своими словами.",
      "— Проверь скобки и знаки на каждом шаге.",
      "— Подставь простые числа для самопроверки.",
      "— Сверь порядок величин в ответе.",
      "— Частые ловушки: забытая скобка, минус, спешка."
    ],
    intermediate: [
      "— Перед началом выпиши триггеры ошибок и анти-паттерны.",
      "— Чек-лист: единицы, границы, частные случаи.",
      "— Сначала упрощай, потом подставляй.",
      "— Проверь альтернативным методом или инвариантом.",
      "— Оформи ключевую лемму."
    ],
    advanced: [
      "— Зафиксируй класс задачи и ограничения.",
      "— Классифицируй ошибки: вычисл./логич./методол.",
      "— Проверь инварианты на каждом шаге.",
      "— Дока: крайние случаи + монотонность → общий случай.",
      "— Придумай контрпример к своему решению."
    ]
  };
  const list = templ[level] || templ.beginner;
  const head = `— Ошибки: ${errors?.slice(0,6).join(", ") || "не указаны"}`;
  return [head, ...list].join("\n");
}
