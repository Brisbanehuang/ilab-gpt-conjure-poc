export function mountOmniImageModelCombobox(): () => void {
  const root = document.querySelector<HTMLElement>("#omniImageModelCombobox");
  const select = document.querySelector<HTMLSelectElement>("#omniImageModel");
  const trigger = document.querySelector<HTMLButtonElement>("#omniImageModelTrigger");
  const label = document.querySelector<HTMLElement>("#omniImageModelValue");
  const list = document.querySelector<HTMLElement>("#omniImageModelOptions");
  if (!root || !select || !trigger || !label || !list) return () => {};

  let open = false;
  let activeIndex = 0;
  const options = () => Array.from(select.options).filter((option) => !option.disabled);

  const close = () => {
    open = false;
    list.classList.add("hidden");
    trigger.setAttribute("aria-expanded", "false");
    trigger.removeAttribute("aria-activedescendant");
  };

  const choose = (index: number) => {
    const option = options()[index];
    if (!option) return;
    select.value = option.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    close();
    trigger.focus();
  };

  const renderOptions = () => {
    list.replaceChildren();
    options().forEach((option, index) => {
      const button = document.createElement("button");
      button.id = `omniImageModelOption-${index}`;
      button.className = "model-combobox-option";
      button.classList.toggle("active", index === activeIndex);
      button.classList.toggle("selected", option.selected);
      button.type = "button";
      button.tabIndex = -1;
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", String(option.selected));
      button.dataset.imageModelOption = option.value;
      button.textContent = option.label;
      button.addEventListener("mousedown", (event) => event.preventDefault());
      button.addEventListener("click", () => choose(index));
      list.appendChild(button);
    });
    trigger.setAttribute("aria-activedescendant", `omniImageModelOption-${activeIndex}`);
  };

  const show = () => {
    open = true;
    activeIndex = Math.max(0, options().findIndex((option) => option.selected));
    renderOptions();
    list.classList.remove("hidden");
    trigger.setAttribute("aria-expanded", "true");
  };

  trigger.addEventListener("click", () => open ? close() : show());
  trigger.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        show();
        return;
      }
      const count = options().length;
      activeIndex = (activeIndex + (event.key === "ArrowDown" ? 1 : -1) + count) % count;
      renderOptions();
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      if (!open) show();
      activeIndex = event.key === "Home" ? 0 : options().length - 1;
      renderOptions();
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (open) choose(activeIndex);
      else show();
    } else if (event.key === "Escape" && open) {
      event.preventDefault();
      close();
    } else if (event.key === "Tab") {
      close();
    }
  });
  document.addEventListener("pointerdown", (event) => {
    if (!root.contains(event.target as Node)) close();
  });
  root.addEventListener("focusout", (event) => {
    if (!root.contains(event.relatedTarget as Node | null)) close();
  });

  // Keep history/session restores on the existing select's value and change path.
  const sync = () => {
    label.textContent = select.selectedOptions[0]?.label || select.value;
    if (open) renderOptions();
  };
  sync();
  return sync;
}
