import type { Metadata } from "next";
import { ReactNode } from "react";
import Shell from "@/components/Shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "FaultScope Control Center",
  description: "See how systems fail. Predict what breaks next. Make them resilient.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
