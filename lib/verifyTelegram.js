// Без импортов. Используем Web Crypto API (HMAC-SHA-256) через globalThis.crypto.subtle

function toHex(buffer) {
  const bytes = new Uint8Array(buffer);
  let hex = "";
  for (let i = 0; i < bytes.length; i++) {
    const h = bytes[i].toString(16).padStart(2, "0");
    hex += h;
  }
  return hex;
}

async function hmacSHA256(keyBytes, dataBytes) {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) throw new Error("Web Crypto unavailable");
  const key = await subtle.importKey(
    "raw",
    keyBytes,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  return await subtle.sign("HMAC", key, dataBytes);
}

/**
 * Проверка подписи initData от Telegram WebApp:
 * secret_key = HMAC_SHA256("WebAppData", botToken)
 * hash_check = HMAC_SHA256(check_string, secret_key)
 */
export async function verifyTelegramInitData(initDataRaw, botToken) {
  if (!initDataRaw || !botToken) return false;

  const params = new URLSearchParams(initDataRaw);
  const data = {};
  for (const [k, v] of params.entries()) data[k] = v;

  const receivedHash = data.hash;
  if (!receivedHash) return false;

  // Сформировать check_string: все key=value (кроме hash), отсортированные по key, через \n
  const checkArr = [];
  for (const key of Object.keys(data).sort()) {
    if (key === "hash") continue;
    checkArr.push(`${key}=${data[key]}`);
  }
  const checkString = checkArr.join("\n");

  const enc = new TextEncoder();

  // 1) secret_key = HMAC_SHA256("WebAppData", botToken)
  const step1 = await hmacSHA256(enc.encode("WebAppData"), enc.encode(botToken));

  // 2) hash_check = HMAC_SHA256(check_string, secret_key)
  const step2 = await hmacSHA256(step1, enc.encode(checkString));
  const calcHex = toHex(step2);

  return calcHex === receivedHash;
}

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
