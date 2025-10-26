/** @type {import('tailwindcss').Config} */
module.exports = {
    darkMode: 'class',
    content: [
        './pages/**/*.{ts,tsx,js,jsx}', // Pages Router (у тебя есть pages/index.tsx и др.)
        './components/**/*.{ts,tsx,js,jsx}', // общие компоненты
        './app/**/*.{ts,tsx,js,jsx}', // если вдруг будут файлы в App Router
    ],
    theme: {
        extend: {
            colors: {
                bg: { DEFAULT: '#0b0e13', soft: '#11161e' },
                card: { DEFAULT: '#121720', soft: '#1a2130' },
            },
            boxShadow: { soft: '0 10px 30px rgba(0,0,0,0.35)' },
            borderRadius: { '2xl': '1rem' },
        },
    },
    plugins: [],
};
