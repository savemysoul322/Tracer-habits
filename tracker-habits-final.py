"""
Трекер привычек

Возможности:
  * добавление и удаление привычек прямо в окне;
  * отметка выполнения за ЛЮБОЙ день — переключение даты кнопками ◀ ▶
    (и кнопка «Сегодня»); галочки относятся к выбранному дню;
  * сохранение данных между запусками (файл ~/.habit_tracker/data.json);
  * алгоритмическое ядро: серия выполнения (streak) и вероятность срыва
    по взвешенному скользящему среднему;
  * тепловая карта активности (холст Canvas);
  * таблица недельной и месячной статистики;
  * экспорт отчёта в CSV.

Почему tkinter: входит в стандартную поставку CPython на Windows и macOS,
не требует сторонних библиотек, одинаково работает на всех ОС.
На части дистрибутивов Linux нужно один раз поставить пакет python3-tk.

Запуск:  python habit_tracker_gui.py
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import random
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

WINDOW = 7          # окно скользящего среднего, дней
PERIOD = 30         # сколько дней показывать в карте/статистике
DATA_FILE = Path.home() / ".habit_tracker" / "data.json"
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "май", "июн",
                "июл", "авг", "сен", "окт", "ноя", "дек"]


# ===========================================================================
#  МОДЕЛЬ ДАННЫХ + СОХРАНЕНИЕ (идентично консольной версии)
# ===========================================================================
class HabitTracker:
    def __init__(self):
        self.habits: list[str] = []
        self.log: dict[tuple[str, str], bool] = {}

    def add_habit(self, name):
        name = name.strip()
        if name and name not in self.habits:
            self.habits.append(name)
            return True
        return False

    def remove_habit(self, name):
        if name in self.habits:
            self.habits.remove(name)
            self.log = {k: v for k, v in self.log.items() if k[0] != name}

    def mark(self, habit, date, done=True):
        self.log[(habit, date.isoformat())] = bool(done)

    def toggle(self, habit, date):
        self.mark(habit, date, not self.is_done(habit, date))

    def is_done(self, habit, date):
        return self.log.get((habit, date.isoformat()), False)

    def to_dict(self):
        nested = {}
        for (habit, d), done in self.log.items():
            if done:
                nested.setdefault(habit, {})[d] = True
        return {"habits": self.habits, "log": nested}

    @classmethod
    def from_dict(cls, data):
        t = cls()
        t.habits = list(data.get("habits", []))
        for habit, days in data.get("log", {}).items():
            for d, done in days.items():
                t.log[(habit, d)] = bool(done)
        return t

    def save(self, path=DATA_FILE):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8")

    @classmethod
    def load(cls, path=DATA_FILE):
        path = Path(path)
        if path.exists():
            try:
                return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
        return cls()


def daterange(start, end):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


# ===========================================================================
#  АЛГОРИТМИЧЕСКОЕ ЯДРО
# ===========================================================================
def series_for(tracker, habit, dates):
    return [1 if tracker.is_done(habit, d) else 0 for d in dates]


def current_streak(tracker, habit, dates):
    s = 0
    for d in reversed(dates):
        if tracker.is_done(habit, d):
            s += 1
        else:
            break
    return s


def longest_streak(tracker, habit, dates):
    best = cur = 0
    for d in dates:
        if tracker.is_done(habit, d):
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def moving_average(series, window=WINDOW):
    if not series:
        return 0.0
    w = series[-window:]
    return sum(w) / len(w)


def failure_probability(series, window=WINDOW):
    """Вероятность срыва = 1 - взвешенное скользящее среднее (свежие дни весят больше)."""
    w = series[-window:]
    if not w:
        return 1.0
    weights = range(1, len(w) + 1)
    wma = sum(v * k for v, k in zip(w, weights)) / sum(weights)
    return round(1.0 - wma, 3)


def completion_rate(series):
    return sum(series) / len(series) if series else 0.0


def fill_synthetic(tracker, days=PERIOD, seed=None):
    rnd = random.Random(seed)
    base_prob = {"Зарядка": 0.75, "Чтение": 0.60, "Вода 2л": 0.85,
                 "Без сахара": 0.50, "Английский": 0.65}
    today = dt.date.today()
    start = today - dt.timedelta(days=days - 1)
    for habit, base in base_prob.items():
        tracker.add_habit(habit)
        prev = 1
        for d in daterange(start, today):
            p = min(0.97, max(0.05, base + (0.15 if prev else -0.15)))
            done = rnd.random() < p
            tracker.mark(habit, d, done)
            prev = 1 if done else 0


# ===========================================================================
#  ГРАФИЧЕСКИЙ ИНТЕРФЕЙС
# ===========================================================================
DONE_COLOR = "#216e39"
MISS_COLOR = "#ebedf0"
SHADES = ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"]


class HabitApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Трекер привычек")
        self.geometry("980x680")
        self.minsize(860, 600)

        self.tracker = HabitTracker.load()      # загрузка сохранённых данных
        self.today = dt.date.today()
        self.start = self.today - dt.timedelta(days=PERIOD - 1)
        self.dates = list(daterange(self.start, self.today))
        self.sel_date = self.today              # день, который сейчас отмечаем
        self.today_vars: dict[str, tk.BooleanVar] = {}

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.refresh()

    # ----- построение интерфейса -----
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        toolbar = ttk.Frame(self, padding=8)
        toolbar.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(toolbar, text="Трекер привычек",
                  font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Экспорт (CSV)",
                   command=self.on_export).pack(side=tk.RIGHT, padx=4)
        ttk.Button(toolbar, text="Синтетика: месяц",
                   command=lambda: self.on_synthetic(PERIOD)).pack(side=tk.RIGHT, padx=4)
        ttk.Button(toolbar, text="Синтетика: неделя",
                   command=lambda: self.on_synthetic(7)).pack(side=tk.RIGHT, padx=4)

        body = ttk.Frame(self, padding=8)
        body.pack(fill=tk.BOTH, expand=True)

        # --- левая колонка: выбранный день + список привычек + добавление ---
        left = ttk.LabelFrame(body, text="Отметки за день", padding=8)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))

        nav = ttk.Frame(left)
        nav.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(nav, text="◀", width=3,
                   command=lambda: self.shift_day(-1)).pack(side=tk.LEFT)
        self.date_label = ttk.Label(nav, text="", width=18, anchor=tk.CENTER,
                                    font=("Segoe UI", 10, "bold"))
        self.date_label.pack(side=tk.LEFT, padx=4)
        ttk.Button(nav, text="▶", width=3,
                   command=lambda: self.shift_day(1)).pack(side=tk.LEFT)
        ttk.Button(nav, text="Сегодня",
                   command=self.go_today).pack(side=tk.LEFT, padx=(6, 0))

        self.habits_frame = ttk.Frame(left)
        self.habits_frame.pack(fill=tk.BOTH, expand=True)

        add = ttk.Frame(left)
        add.pack(fill=tk.X, pady=(8, 0))
        self.new_habit = ttk.Entry(add)
        self.new_habit.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.new_habit.bind("<Return>", lambda e: self.on_add())
        ttk.Button(add, text="+ Добавить", command=self.on_add).pack(side=tk.LEFT, padx=(4, 0))

        # --- правая колонка: карта + статистика ---
        right = ttk.Frame(body)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        hm = ttk.LabelFrame(right, text="Тепловая карта активности", padding=8)
        hm.pack(side=tk.TOP, fill=tk.X)
        mode_bar = ttk.Frame(hm)
        mode_bar.pack(fill=tk.X, pady=(0, 4))
        self.heatmap_mode = tk.StringVar(value="linear")
        ttk.Radiobutton(mode_bar, text="Линейная (30 дней)", value="linear",
                        variable=self.heatmap_mode,
                        command=self._draw_heatmap).pack(side=tk.LEFT)
        ttk.Radiobutton(mode_bar, text="Недельная (календарь)", value="weekly",
                        variable=self.heatmap_mode,
                        command=self._draw_heatmap).pack(side=tk.LEFT, padx=10)
        self.canvas = tk.Canvas(hm, height=200, bg="white", highlightthickness=0)
        self.canvas.pack(fill=tk.X, expand=True)

        st = ttk.LabelFrame(right, text="Статистика", padding=8)
        st.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(8, 0))
        cols = ("habit", "week", "month", "cur", "long", "fail")
        headers = {"habit": "Привычка", "week": "Неделя %", "month": "Месяц %",
                   "cur": "Тек.серия", "long": "Макс.серия", "fail": "P(срыв)"}
        widths = {"habit": 150, "week": 90, "month": 90,
                  "cur": 90, "long": 100, "fail": 90}
        self.tree = ttk.Treeview(st, columns=cols, show="headings", height=8)
        for col in cols:
            self.tree.heading(col, text=headers[col])
            self.tree.column(col, width=widths[col],
                             anchor=(tk.W if col == "habit" else tk.CENTER))
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.tag_configure("risk", foreground="#b00020")

        self.status = ttk.Label(self, text="", padding=6, anchor=tk.W)
        self.status.pack(side=tk.BOTTOM, fill=tk.X)

    # ----- действия с данными -----
    def shift_day(self, delta):
        new = self.sel_date + dt.timedelta(days=delta)
        if self.start <= new <= self.today:   # в пределах окна 30 дней
            self.sel_date = new
            self.refresh()

    def go_today(self):
        self.sel_date = self.today
        self.refresh()

    def on_add(self):
        name = self.new_habit.get()
        if self.tracker.add_habit(name):
            self.new_habit.delete(0, tk.END)
            self.tracker.save()
            self.refresh()
        else:
            messagebox.showwarning("Добавление",
                                   "Введите непустое уникальное название.")

    def remove_habit(self, habit):
        if messagebox.askyesno("Удаление", f"Удалить «{habit}» и всю историю?"):
            self.tracker.remove_habit(habit)
            self.tracker.save()
            self.refresh()

    def toggle_habit(self, habit):
        self.tracker.mark(habit, self.sel_date, self.today_vars[habit].get())
        self.tracker.save()
        self.refresh()

    def on_synthetic(self, days=PERIOD):
        period = "неделю (7 дн.)" if days == 7 else f"{days} дней"
        if messagebox.askyesno("Синтетика",
                               f"Добавить демо-данные за {period} к текущим привычкам?"):
            fill_synthetic(self.tracker, days, seed=42)
            self.tracker.save()
            self.refresh()

    def on_export(self):
        if not self.tracker.habits:
            messagebox.showinfo("Экспорт", "Нет привычек для экспорта.")
            return
        path = filedialog.asksaveasfilename(
            title="Сохранить отчёт", defaultextension=".csv",
            initialfile=f"habits_{dt.datetime.now():%Y%m%d_%H%M%S}.csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Дата"] + self.tracker.habits)
            for d in self.dates:
                w.writerow([d.isoformat()] +
                           [int(self.tracker.is_done(h, d)) for h in self.tracker.habits])
        messagebox.showinfo("Экспорт", f"Отчёт сохранён:\n{Path(path)}")

    def _on_close(self):
        self.tracker.save()
        self.destroy()

    # ----- перерисовка -----
    def refresh(self):
        self._build_day_panel()
        self._draw_heatmap()
        self._fill_stats()
        if self.tracker.habits:
            avg = sum(completion_rate(series_for(self.tracker, h, self.dates))
                      for h in self.tracker.habits) / len(self.tracker.habits)
            disc = f"Средняя дисциплина: {avg*100:.0f}%"
        else:
            disc = "Привычек нет — добавьте первую слева."
        self.status.config(
            text=f"Период: {self.start} .. {self.today} ({len(self.dates)} дн.)   {disc}")

    def _build_day_panel(self):
        # подпись выбранной даты
        wd = WEEKDAYS[self.sel_date.weekday()]
        suffix = " (сегодня)" if self.sel_date == self.today else ""
        self.date_label.config(text=f"{wd}, {self.sel_date.isoformat()}{suffix}")

        for w in self.habits_frame.winfo_children():
            w.destroy()
        self.today_vars.clear()

        if not self.tracker.habits:
            ttk.Label(self.habits_frame,
                      text="Список пуст.\nДобавьте привычку ниже ↓",
                      foreground="#777").pack(pady=12)
            return

        for habit in self.tracker.habits:
            row = ttk.Frame(self.habits_frame)
            row.pack(fill=tk.X, pady=3)
            var = tk.BooleanVar(value=self.tracker.is_done(habit, self.sel_date))
            self.today_vars[habit] = var
            ttk.Checkbutton(row, text=habit, variable=var,
                            command=lambda h=habit: self.toggle_habit(h)).pack(side=tk.LEFT)
            tk.Button(row, text="✕", width=2, relief=tk.FLAT, fg="#b00020",
                      command=lambda h=habit: self.remove_habit(h)).pack(side=tk.RIGHT)
            fp = failure_probability(series_for(self.tracker, habit, self.dates))
            tk.Label(row, text=f"P={fp:.0%}",
                     fg=("#b00020" if fp >= 0.5 else "#216e39")).pack(side=tk.RIGHT, padx=4)

    def _draw_heatmap(self):
        self.canvas.delete("all")
        if not self.tracker.habits:
            self.canvas.create_text(20, 20, text="Нет данных", anchor=tk.W, fill="#999")
            return
        if self.heatmap_mode.get() == "weekly":
            self._draw_weekly()
        else:
            self._draw_linear()

    def _draw_linear(self):
        habits = self.tracker.habits
        cell, gap, lx, ty = 16, 2, 110, 36
        # подписи месяцев над первым днём каждого месяца
        last_month = None
        for ci, d in enumerate(self.dates):
            if d.month != last_month:
                x = lx + ci * (cell + gap)
                self.canvas.create_text(x, ty - 24, text=MONTHS_SHORT[d.month - 1],
                                        anchor=tk.W, font=("Segoe UI", 8, "bold"),
                                        fill="#555")
                last_month = d.month
        # числовая ось дат: день месяца над каждым столбцом
        for ci, d in enumerate(self.dates):
            x = lx + ci * (cell + gap)
            color = "#ff8c00" if d == self.sel_date else "#999"
            self.canvas.create_text(x + cell / 2, ty - 12, text=str(d.day),
                                    font=("Segoe UI", 7), fill=color)
        for r, habit in enumerate(habits):
            y = ty + r * (cell + gap)
            self.canvas.create_text(lx - 6, y + cell / 2, text=habit[:14],
                                    anchor=tk.E, font=("Segoe UI", 9))
            for ci, d in enumerate(self.dates):
                x = lx + ci * (cell + gap)
                fill = DONE_COLOR if self.tracker.is_done(habit, d) else MISS_COLOR
                outline = "#ff8c00" if d == self.sel_date else "white"
                self.canvas.create_rectangle(x, y, x + cell, y + cell,
                                             fill=fill, outline=outline)
        y = ty + len(habits) * (cell + gap) + 6
        self.canvas.create_text(lx - 6, y + cell / 2, text="ИТОГО/день",
                                anchor=tk.E, font=("Segoe UI", 9, "bold"))
        n = len(habits)
        for ci, d in enumerate(self.dates):
            x = lx + ci * (cell + gap)
            done = sum(1 for h in habits if self.tracker.is_done(h, d))
            idx = min(len(SHADES) - 1, round(done / n * (len(SHADES) - 1)))
            self.canvas.create_rectangle(x, y, x + cell, y + cell,
                                         fill=SHADES[idx], outline="white")

    def _draw_weekly(self):
        """Календарный формат: строки — дни недели (Пн..Вс), столбцы — недели.
        Цвет клетки — доля выполненных привычек за день (агрегат)."""
        habits = self.tracker.habits
        n = len(habits)
        raw_start = self.today - dt.timedelta(days=PERIOD - 1)
        start = raw_start - dt.timedelta(days=raw_start.weekday())  # понедельник
        weeks = (self.today - start).days // 7 + 1
        cell, gap, lx, ty = 22, 4, 44, 26

        # подписи дней недели слева
        for r in range(7):
            y = ty + r * (cell + gap)
            self.canvas.create_text(lx - 6, y + cell / 2, text=WEEKDAYS[r],
                                    anchor=tk.E, font=("Segoe UI", 9))
        # подписи месяцев сверху (при смене месяца)
        last_month = None
        for w in range(weeks):
            d0 = start + dt.timedelta(days=w * 7)
            x = lx + w * (cell + gap)
            if d0.month != last_month:
                self.canvas.create_text(x + cell / 2, ty - 12,
                                        text=MONTHS_SHORT[d0.month - 1],
                                        font=("Segoe UI", 8), fill="#777")
                last_month = d0.month
        # клетки
        for w in range(weeks):
            for r in range(7):
                d = start + dt.timedelta(days=w * 7 + r)
                if d > self.today:
                    continue
                x = lx + w * (cell + gap)
                y = ty + r * (cell + gap)
                done = sum(1 for h in habits if self.tracker.is_done(h, d))
                idx = min(len(SHADES) - 1, round(done / n * (len(SHADES) - 1))) if n else 0
                outline = "#ff8c00" if d == self.sel_date else "white"
                self.canvas.create_rectangle(x, y, x + cell, y + cell,
                                             fill=SHADES[idx], outline=outline, width=2)
                # число дня внутри клетки (белое на тёмном фоне, тёмное на светлом)
                txt_color = "white" if idx >= 3 else "#555"
                self.canvas.create_text(x + cell / 2, y + cell / 2, text=str(d.day),
                                        fill=txt_color, font=("Segoe UI", 8))

    def _fill_stats(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        week = self.dates[-7:]
        for habit in self.tracker.habits:
            s_month = series_for(self.tracker, habit, self.dates)
            s_week = series_for(self.tracker, habit, week)
            fp = failure_probability(s_month)
            values = (habit,
                      f"{completion_rate(s_week)*100:.0f}",
                      f"{completion_rate(s_month)*100:.0f}",
                      current_streak(self.tracker, habit, self.dates),
                      longest_streak(self.tracker, habit, self.dates),
                      f"{fp:.2f}")
            self.tree.insert("", tk.END, values=values,
                             tags=("risk",) if fp >= 0.5 else ())


def main():
    HabitApp().mainloop()


if __name__ == "__main__":
    main()
