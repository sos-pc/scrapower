// Minimal UI for the browser worker.

export interface UIState {
  connected: boolean;
  tasksCompleted: number;
  cpuTimeSec: number;
  dataMb: number;
}

const CSS = `
#scrapower-widget {
  position: fixed;
  bottom: 16px;
  right: 16px;
  width: 280px;
  background: #1a1a2e;
  color: #e0e0e0;
  border-radius: 12px;
  padding: 16px;
  font-family: system-ui, sans-serif;
  font-size: 13px;
  box-shadow: 0 4px 24px rgba(0,0,0,0.4);
  z-index: 99999;
}
#scrapower-widget .status {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
#scrapower-widget .dot {
  width: 10px; height: 10px; border-radius: 50%;
  background: #f00; transition: background 0.3s;
}
#scrapower-widget .dot.on { background: #0f0; }
#scrapower-widget .stats { line-height: 1.8; margin-bottom: 12px; }
#scrapower-widget .toggle {
  width: 100%; padding: 8px; border: none; border-radius: 6px;
  cursor: pointer; font-weight: bold; font-size: 14px;
  background: #0f0; color: #000;
}
#scrapower-widget .toggle.off { background: #f00; color: #fff; }
`;

export function createUI(): {
  update: (state: Partial<UIState>) => void;
  onToggle: (cb: (active: boolean) => void) => void;
} {
  const style = document.createElement("style");
  style.textContent = CSS;
  document.head.appendChild(style);

  const widget = document.createElement("div");
  widget.id = "scrapower-widget";
  widget.innerHTML = `
    <div class="status">
      <div class="dot off"></div>
      <span class="state-text">Déconnecté</span>
    </div>
    <div class="stats">Tâches : <span class="tasks">0</span><br>GPU : <span class="gpu-label"></span><br>CPU : <span class="cpu">0.0</span>s<br>Données : <span class="data">0.0</span> Mo</div>
    <button class="toggle on">● Actif</button>
  `;
  document.body.appendChild(widget);

  const dot = widget.querySelector(".dot")!;
  const stateText = widget.querySelector(".state-text")!;
  const tasksEl = widget.querySelector(".tasks")!;
  const cpuEl = widget.querySelector(".cpu")!;
  const dataEl = widget.querySelector(".data")!;
  const toggleBtn = widget.querySelector(".toggle")! as HTMLButtonElement;

  let active = true;
  let toggleCallback: ((active: boolean) => void) | null = null;
  const gpuLabel = widget.querySelector(".gpu-label")!;
  (async () => {
    gpuLabel.textContent = (navigator as any).gpu ? "GPU: WebGPU" : "";
    try {
      const a = await (navigator as any).gpu?.requestAdapter();
      if (a) {
        const i = await a.requestAdapterInfo();
        gpuLabel.textContent = "GPU: " + (i.architecture || "WebGPU");
      }
    } catch {}
  })();

  toggleBtn.addEventListener("click", () => {
    active = !active;
    toggleBtn.textContent = active ? "● Actif" : "○ Inactif";
    toggleBtn.className = `toggle ${active ? "on" : "off"}`;
    if (active) {
      dot.className = "dot on";
      stateText.textContent = "Connecté";
    } else {
      dot.className = "dot";
      stateText.textContent = "En pause";
    }
    toggleCallback?.(active);
  });

  return {
    update(state: Partial<UIState>) {
      if (state.connected !== undefined) {
        dot.className = `dot ${state.connected ? "on" : ""}`;
        stateText.textContent = state.connected ? "Connecté" : "Déconnecté";
      }
      if (state.tasksCompleted !== undefined) {
        tasksEl.textContent = String(state.tasksCompleted);
      }
      if (state.cpuTimeSec !== undefined) {
        cpuEl.textContent = state.cpuTimeSec.toFixed(1);
      }
      if (state.dataMb !== undefined) {
        dataEl.textContent = state.dataMb.toFixed(1);
      }
    },
    onToggle(cb: (active: boolean) => void) {
      toggleCallback = cb;
    },
  };
}
