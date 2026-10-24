"use client";

import { useEffect, useState } from "react";
import { API_URL } from "../lib/api";

export function Banner() {
  const [text, setText] = useState(
    "Mock mode — no live iMessage or email delivery. Supplier mail stays in the sandbox.",
  );
  useEffect(() => {
    fetch(`${API_URL}/api/meta`)
      .then((response) => response.json())
      .then((meta) => {
        if (meta.banner) setText(meta.banner);
      })
      .catch(() => undefined);
  }, []);
  return (
    <div className="banner" data-testid="mock-banner">
      {text}
    </div>
  );
}
