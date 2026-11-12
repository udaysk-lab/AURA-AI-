import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "@/lib/auth";
import { AssistantProvider } from "@/lib/assistant";

// Every page title ends in "· AURA". The permission checks for screen control
// rely on that to recognise AURA's own window and keep out of it, so a page
// that sets its own title must go through this template.
export const metadata: Metadata = {
  title: { default: "Your personal employee · AURA", template: "%s · AURA" },
  description:
    "An assistant that learns how you work, handles your email, calendar and commitments, and keeps going when you're away.",
};

// Applies the saved theme before first paint so there is no flash of the wrong
// palette. Dark aurora is the root palette, so only `warm` needs a class.
// Deliberately inline and dependency-free.
const THEME_BOOTSTRAP = `
(function(){
  try {
    var t = localStorage.getItem('aura_theme') || 'dark';
    document.documentElement.classList.toggle('warm', t === 'warm');
    document.documentElement.classList.toggle('dark', t !== 'warm');
  } catch (e) {}
})();
`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      <body>
        <AuthProvider>
          <AssistantProvider>
            <div className="relative z-10">{children}</div>
          </AssistantProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
