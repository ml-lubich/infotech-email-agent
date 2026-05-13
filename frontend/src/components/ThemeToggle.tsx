import { useEffect, useState } from "react";

type Theme = "dark" | "light";

const STORAGE_KEY = "iia-theme";

function readInitialTheme(): Theme {
    if (typeof window === "undefined") return "dark";
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === "dark" || stored === "light") return stored;
    const prefersLight = window.matchMedia(
        "(prefers-color-scheme: light)",
    ).matches;
    return prefersLight ? "light" : "dark";
}

export function ThemeToggle() {
    const [theme, setTheme] = useState<Theme>(readInitialTheme);

    useEffect(() => {
        document.documentElement.setAttribute("data-theme", theme);
        document.documentElement.style.colorScheme = theme;
        window.localStorage.setItem(STORAGE_KEY, theme);
    }, [theme]);

    // Follow OS changes only when the user has not pinned a choice yet —
    // once they click the toggle we respect their pin permanently.
    useEffect(() => {
        if (window.localStorage.getItem(STORAGE_KEY)) return;
        const mq = window.matchMedia("(prefers-color-scheme: light)");
        const handler = (e: MediaQueryListEvent) =>
            setTheme(e.matches ? "light" : "dark");
        mq.addEventListener("change", handler);
        return () => mq.removeEventListener("change", handler);
    }, []);

    const next: Theme = theme === "dark" ? "light" : "dark";
    const label = `Switch to ${next} theme`;

    return (
        <button
            type="button"
            className="theme-toggle"
            onClick={() => setTheme(next)}
            aria-label={label}
            title={label}
        >
            {theme === "dark" ? (
                // Sun
                <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden>
                    <circle cx="12" cy="12" r="4.2" fill="currentColor" />
                    <g
                        stroke="currentColor"
                        strokeWidth="1.8"
                        strokeLinecap="round"
                    >
                        <line x1="12" y1="2.6" x2="12" y2="5" />
                        <line x1="12" y1="19" x2="12" y2="21.4" />
                        <line x1="2.6" y1="12" x2="5" y2="12" />
                        <line x1="19" y1="12" x2="21.4" y2="12" />
                        <line x1="5.2" y1="5.2" x2="6.9" y2="6.9" />
                        <line x1="17.1" y1="17.1" x2="18.8" y2="18.8" />
                        <line x1="5.2" y1="18.8" x2="6.9" y2="17.1" />
                        <line x1="17.1" y1="6.9" x2="18.8" y2="5.2" />
                    </g>
                </svg>
            ) : (
                // Moon
                <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden>
                    <path
                        d="M20.5 14.4A8.5 8.5 0 1 1 9.6 3.5a7 7 0 0 0 10.9 10.9z"
                        fill="currentColor"
                    />
                </svg>
            )}
            <span className="theme-toggle-label">
                {theme === "dark" ? "Light" : "Dark"}
            </span>
        </button>
    );
}
