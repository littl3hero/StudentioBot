import crypto from "crypto";

/**
 * verifyTelegramInitData(initDataRaw: string, botToken: string): boolean
 * initDataRaw — строка window.Telegram.WebApp.initData
 */
export function verifyTelegramInitData(initDataRaw, botToken) {
  if (!initDataRaw || !botToken) return false;

  // Разобрать querystring в пары
  const urlParams = new URLSearchParams(initDataRaw);
  const data = {};
  for (const [k, v] of urlParams.entries()) data[k] = v;

  const hash = data.hash;
  if (!hash) return false;

  // Сформировать проверочную строку (все, кроме hash)
  const checkArr = [];
  for (const key of Object.keys(data).sort()) {
    if (key === "hash") continue;
    checkArr.push(`${key}=${data[key]}`);
  }
  const checkString = checkArr.join("\n");

  // Секрет = HMAC_SHA256("WebAppData", botToken)
  const secretKey = crypto
    .createHmac("sha256", "WebAppData")
    .update(botToken)
    .digest();

  // HMAC_SHA256(checkString, secretKey) hex
  const hmac = crypto
    .createHmac("sha256", secretKey)
    .update(checkString)
    .digest("hex");

  return hmac === hash;
}

/** Возвращает user.id из initData (если есть) */
export function extractUserId(initDataRaw) {
  try {
    const params = new URLSearchParams(initDataRaw);
    const userJson = params.get("user");
    if (!userJson) return null;
    const user = JSON.parse(userJson);
    return String(user.id);
  } catch {
    return null;
  }
}
