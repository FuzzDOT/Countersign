import { useState, useEffect } from "react";
import { getCookie, setCookie } from "../lib/cookies";

const COOKIE_NAME = "rail_expanded";

export function useRailState() {
  const [expanded, setExpanded] = useState(() => getCookie(COOKIE_NAME) === "true");

  useEffect(() => {
    setCookie(COOKIE_NAME, String(expanded));
  }, [expanded]);

  return { expanded, toggle: () => setExpanded((e) => !e) };
}