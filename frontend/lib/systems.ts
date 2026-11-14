"use client";

import { useEffect, useState } from "react";
import { api } from "./api";

let names: Record<string, string> | null = null;
let inflight: Promise<Record<string, string>> | null = null;

function load(): Promise<Record<string, string>> {
  if (names) return Promise.resolve(names);
  inflight ??= api
    .plugins()
    .then((list) => {
      names = Object.fromEntries(list.map((p) => [p.id, p.name]));
      return names;
    })
    .catch(() => {
      inflight = null; // try again next time
      return {} as Record<string, string>;
    });
  return inflight;
}

/** "core-tasks" -> "Core tasks": the fallback when the name isn't known. */
export function tidySystemId(id: string) {
  const t = id.replace(/[-_.]+/g, " ").trim();
  return t ? t.charAt(0).toUpperCase() + t.slice(1) : id;
}

/** Display names of systems, loaded once from the Systems list. Returns a lookup. */
export function useSystemNames(): (id: string) => string {
  const [map, setMap] = useState<Record<string, string>>(names ?? {});
  useEffect(() => {
    let live = true;
    void load().then((m) => live && setMap(m));
    return () => {
      live = false;
    };
  }, []);
  return (id: string) => map[id] || tidySystemId(id);
}
