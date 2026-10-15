"use client";

import { useEffect, useState } from "react";
import { CatalogSkill, SkillCatalog, api } from "@/lib/api";

/**
 * The skill catalogue as the backend has it (GET /api/skills/catalog), so the
 * landing page can't drift from the product again — it did, twice, while it
 * kept its own list. The fallback is only what renders before the request
 * answers, and matches the catalogue at the time of writing.
 */
const FALLBACK: SkillCatalog = {
  skills: [],
  counts: { skills: 28, device_skills: 7, tools: 60 },
};

let request: Promise<SkillCatalog> | null = null;

export function useSkillCatalog(): SkillCatalog {
  const [catalog, setCatalog] = useState<SkillCatalog>(FALLBACK);
  useEffect(() => {
    let live = true;
    request ??= api.skillCatalog().catch(() => FALLBACK);
    void request.then((c) => live && setCatalog(c));
    return () => {
      live = false;
    };
  }, []);
  return catalog;
}

export function SkillCount({ kind = "skills" }: { kind?: "skills" | "browser" | "device" | "tools" }) {
  const { counts } = useSkillCatalog();
  const value =
    kind === "browser"
      ? counts.skills - counts.device_skills
      : kind === "device"
        ? counts.device_skills
        : kind === "tools"
          ? counts.tools
          : counts.skills;
  return <>{value}</>;
}

const FALLBACK_CODES = [
  "EM01", "EM02", "EM03", "CA01", "CA02", "CA03", "MP01", "TK01", "TK02", "MM01",
  "CT01", "BR01", "NT01", "SY01", "RS01", "RS02", "DC01", "DC02", "DL01", "DL02",
  "FG01", "DV01", "DV02", "DV03", "DV04", "DV05", "DV06", "DV07",
];

export function SkillChips() {
  const { skills } = useSkillCatalog();
  const list: Array<Pick<CatalogSkill, "code" | "name" | "device">> = skills.length
    ? skills
    : FALLBACK_CODES.map((code) => ({ code, name: code, device: code.startsWith("DV") }));
  return (
    <div className="flex flex-wrap gap-1.5">
      {list.map((s) => (
        <span
          key={s.code}
          title={s.device ? `${s.name} — on your computer` : s.name}
          className={`chip font-mono ${
            s.device
              ? "border-teal/30 bg-teal/10 text-teal"
              : "border-accent/25 bg-accent-dim text-accent-soft"
          }`}
        >
          {s.code}
        </span>
      ))}
    </div>
  );
}
