import { defineComponent, h, ref } from "vue";

const names = { classic: "Исходная", noir: "Обсидиан", bordeaux: "Бордо" };
type Theme = keyof typeof names;
const storageKey = "vidharbor.portal.theme";
const defaultTheme: Theme = "classic";
export const theme = ref<Theme>(defaultTheme);

function apply(value: string | null) {
  theme.value = value && Object.hasOwn(names, value) ? value as Theme : defaultTheme;
  document.documentElement.dataset.theme = theme.value;
}

try {
  apply(localStorage.getItem(storageKey));
} catch {
  apply(null);
}
window.addEventListener("storage", (event) => {
  if (event.key === storageKey || event.key === null) apply(event.newValue);
});

export const ThemePicker = defineComponent({
  setup() {
    return () => h("label", { class: "theme-picker" }, [
      h("span", "Оформление"),
      h("select", {
        value: theme.value,
        onChange: (event: Event) => {
          apply((event.target as HTMLSelectElement).value);
          try {
            localStorage.setItem(storageKey, theme.value);
          } catch { /* The current page still uses the chosen theme. */ }
        },
      }, Object.entries(names).map(([value, label]) => h("option", { value }, label))),
    ]);
  },
});
