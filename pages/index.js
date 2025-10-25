import { useEffect, useMemo, useState } from "react";
import Head from "next/head";

export default function Home() {
  const [tg, setTg] = useState(null);
  const [initData, setInitData] = useState("");
  const [level, setLevel] = useState("beginner");
  const [errorsText, setErrorsText] = useState("");
  const [advice, setAdvice] = useState("");
  const [profile, setProfile] = useState({ level: "beginner", errors: [] });

  // Загрузка Telegram WebApp
  useEffect(() => {
    if (typeof window === "undefined") return;
    const t = window.Telegram?.WebApp || null;
    setTg(t || null);
    if (t) {
      // применим тему
      document.documentElement.classList.toggle("tg-dark", t.colorScheme === "dark");
      t.expand();
      setInitData(t.initData || "");
    }
  }, []);
  useEffect(() => {
  if (typeof window !== "undefined" && window.MathJax) {
    window.MathJax.typesetPromise?.();
  }
}, [advice]);
  // Инициализировать профиль из localStorage
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const raw = localStorage.getItem("curator_profile");
      if (raw) {
        const p = JSON.parse(raw);
        setProfile(p);
        setLevel(p.level || "beginner");
        setErrorsText((p.errors || []).join(", "));
      }
    } catch {}
  }, []);

  // Сохранение профиля в localStorage
  const saveProfile = (p) => {
    setProfile(p);
    try { localStorage.setItem("curator_profile", JSON.stringify(p)); } catch {}
  };

  const parseErrors = (txt) => {
    const chunks = txt.replaceAll(";", ",").split(",");
    const arr = [];
    chunks.forEach(c => {
      c.split("\n").forEach(s => {
        const v = s.trim().replace(/^[\-—\*]\s*/, "");
        if (v && v.length <= 200) arr.push(v);
      });
    });
    // дедуп и усечём
    const seen = new Set();
    const out = [];
    arr.forEach(e => {
      const k = e.toLowerCase();
      if (!seen.has(k)) { seen.add(k); out.push(e); }
    });
    return out.slice(0, 20);
  };

  const saveLevel = () => {
    const p = { ...profile, level };
    saveProfile(p);
    haptic("success");
  };

  const saveErrors = () => {
    const list = parseErrors(errorsText);
    const existing = new Set((profile.errors || []).map(e => e.toLowerCase()));
    const merged = [...(profile.errors || [])];
    list.forEach(e => { if (!existing.has(e.toLowerCase())) merged.push(e); });
    const p = { ...profile, errors: merged };
    saveProfile(p);
    haptic("success");
  };

  const haptic = (type) => {
    try { tg?.HapticFeedback?.notificationOccurred(type || "success"); } catch {}
  };

  const requestTip = async () => {
    setAdvice("Готовлю шпаргалку…");
    try {
      const body = {
        level: profile.level || "beginner",
        errors: profile.errors || [],
        initData: initData || ""
      };
      const r = await fetch("/api/tip", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      const data = await r.json();
      if (!r.ok || !data.ok) throw new Error(data.error || "api error");
      setAdvice(data.advice || "— (пусто)");
      haptic("success");
    } catch (e) {
      setAdvice("Ошибка: " + (e.message || "unknown"));
    }
  };

  return (
    <>
      <Head>
        <title>Куратор — MiniApp</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        {/* Telegram WebApp SDK */}
        <script src="https://telegram.org/js/telegram-web-app.js" />
        <script
        id="mathjax"
      async
        src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"
      ></script>
        <link rel="stylesheet" href="/styles.css" />
      </Head>
      <div className="container">
        <h1>Куратор</h1>
        <p className="muted">Мини-шпаргалки под твои ошибки</p>

        <section className="card">
          <label>Уровень:</label>
          <select value={level} onChange={e => setLevel(e.target.value)}>
            <option value="beginner">beginner</option>
            <option value="intermediate">intermediate</option>
            <option value="advanced">advanced</option>
          </select>
          <button onClick={saveLevel}>Сохранить уровень</button>
        </section>

        <section className="card">
          <label>Твои типичные ошибки (через запятую или с новой строки):</label>
          <textarea
            rows={5}
            value={errorsText}
            placeholder="пропуск скобок, ошибка знака, неверное приведение дробей"
            onChange={e => setErrorsText(e.target.value)}
          />
          <button onClick={saveErrors}>Добавить ошибки</button>
        </section>

        <section className="card">
          <button onClick={() => {
            // показать локальный профиль
            const p = profile;
            const txt = `Профиль:\n— уровень: ${p.level}\n— ошибки (${(p.errors||[]).length}): ${(p.errors||[]).join(", ") || "—"}`;
            setAdvice(txt);
          }}>
            Показать профиль
          </button>
          <button className="primary" onClick={requestTip}>Сгенерировать шпаргалку</button>
        </section>

        <section className="card">
          <pre className="out">{advice}</pre>
        </section>
      </div>
    </>
  );
}
