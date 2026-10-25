import * as React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";

Office.onReady((info) => {
  const root = createRoot(document.getElementById("root") as HTMLElement);
  if (info.host !== Office.HostType.Excel) {
    root.render(<p className="error">WorldState runs inside Excel. Open this add-in from the Excel ribbon.</p>);
    return;
  }
  root.render(<App />);
});
