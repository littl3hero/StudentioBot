/** @type {import('tailwindcss').Config} */
module.exports = {
    darkMode: 'class',
    content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
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
