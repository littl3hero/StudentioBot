'use client';
import Link from 'next/link';
import { useRouter } from 'next/router';
import { useState } from 'react';
import { Menu, Home, User, BookOpenCheck, Library } from 'lucide-react';

const nav = [
    { href: '/', label: 'Главная', icon: Home },
    { href: '/student', label: 'Ученик', icon: User },
    { href: '/tests', label: 'Тесты', icon: BookOpenCheck },
    { href: '/materials', label: 'Материалы', icon: Library },
];

export function Sidebar() {
    const router = useRouter();
    const [open, setOpen] = useState(false);

    return (
        <>
            {/* Desktop */}
            <aside className="hidden md:flex md:w-[280px] md:flex-col md:gap-2 border-r border-white/5 bg-bg/60 backdrop-blur sticky top-0 h-screen p-4">
                <div className="mb-4">
                    <Link
                        href="/"
                        className="inline-flex items-center gap-2 font-semibold"
                    >
                        <span className="px-2 py-1 rounded-lg bg-white/10 text-sm">
                            Studentio
                        </span>
                        <span className="text-white/60 text-sm">UI</span>
                    </Link>
                </div>
                <nav className="space-y-1">
                    {nav.map(({ href, label, icon: Icon }) => {
                        const active = router.pathname === href;
                        return (
                            <Link
                                key={href}
                                href={href}
                                className={`flex items-center gap-3 px-3 py-2 rounded-xl transition border ${
                                    active
                                        ? 'bg-white/10 border-white/15'
                                        : 'hover:bg-white/5 border-transparent'
                                }`}
                            >
                                <Icon className="size-4 shrink-0" />
                                <span className="text-sm">{label}</span>
                            </Link>
                        );
                    })}
                </nav>
                <div className="mt-auto text-xs text-white/40">
                    Тёмная тема · минимализм
                </div>
            </aside>

            {/* Mobile topbar + drawer */}
            <div className="md:hidden fixed top-0 left-0 right-0 z-40 bg-bg/80 backdrop-blur border-b border-white/5">
                <div className="h-14 flex items-center gap-3 px-4">
                    <button
                        aria-label="Открыть меню"
                        className="p-2 rounded-lg border border-white/10 hover:bg-white/5"
                        onClick={() => setOpen(true)}
                    >
                        <Menu className="size-4" />
                    </button>
                    <Link href="/" className="font-semibold">
                        Studentio
                    </Link>
                </div>
            </div>

            {/* Drawer */}
            <div
                className={`md:hidden fixed inset-0 z-50 transition ${
                    open ? 'pointer-events-auto' : 'pointer-events-none'
                }`}
            >
                <div
                    className={`absolute inset-0 bg-black/50 transition ${
                        open ? 'opacity-100' : 'opacity-0'
                    }`}
                    onClick={() => setOpen(false)}
                />
                <div
                    className={`absolute top-0 bottom-0 left-0 w-[78%] max-w-[320px] bg-bg shadow-soft border-r border-white/5 transform transition-transform duration-300 ${
                        open ? 'translate-x-0' : '-translate-x-full'
                    }`}
                >
                    <div className="h-14 flex items-center px-4 border-b border-white/5 font-semibold">
                        Навигация
                    </div>
                    <nav className="p-2">
                        {nav.map(({ href, label, icon: Icon }) => {
                            const active = router.pathname === href;
                            return (
                                <Link
                                    key={href}
                                    href={href}
                                    className={`flex items-center gap-3 px-3 py-3 rounded-xl transition border ${
                                        active
                                            ? 'bg-white/10 border-white/15'
                                            : 'hover:bg-white/5 border-transparent'
                                    }`}
                                    onClick={() => setOpen(false)}
                                >
                                    <Icon className="size-4 shrink-0" />
                                    <span className="text-sm">{label}</span>
                                </Link>
                            );
                        })}
                    </nav>
                </div>
            </div>
            {/* spacer for mobile topbar */}
            <div className="md:hidden h-14" />
        </>
    );
}
