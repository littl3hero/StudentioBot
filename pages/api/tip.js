import { verifyTelegramInitData, extractUserId } from "../../lib/verifyTelegram";
import { generateTip } from "../../lib/llm";

export const config = {
  runtime: "nodejs" // (по умолчанию) — подойдёт для Vercel
};

export default async function handler(req, res) {
  try {
    if (req.method !== "POST") {
      return res.status(405).json({ ok: false, error: "Method Not Allowed" });
    }

    const { level, errors, initData } = req.body || {};
    if (!initData) return res.status(400).json({ ok: false, error: "initData required" });

    const ok = verifyTelegramInitData(initData, process.env.TELEGRAM_BOT_TOKEN || "");
    if (!ok) return res.status(401).json({ ok: false, error: "invalid initData" });

    // можно логировать user_id (просто для статистики)
    const userId = extractUserId(initData) || "unknown";

    const normLevel = (String(level || "beginner").toLowerCase());
    const allowed = new Set(["beginner", "intermediate", "advanced"]);
    const finalLevel = allowed.has(normLevel) ? normLevel : "beginner";

    const errs = Array.isArray(errors)
      ? errors.slice(0, 20).map(s => String(s).slice(0, 200))
      : [];

    const advice = await generateTip({ level: finalLevel, errors: errs });
    return res.status(200).json({ ok: true, advice, userId });
  } catch (e) {
    console.error(e);
    return res.status(500).json({ ok: false, error: "server_error" });
  }
}
