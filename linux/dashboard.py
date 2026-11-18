#!/usr/bin/python3
"""Unprivileged GTK dashboard. Privileges are requested only for VPN control."""
import collections
import concurrent.futures
import json
import os
from pathlib import Path
import re
import subprocess
import time

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from appcore import PUBLIC, read_json


def command(args, timeout=8):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def size(value):
    for unit in ('Б', 'КБ', 'МБ', 'ГБ', 'ТБ'):
        if abs(value) < 1024 or unit == 'ТБ':
            return f'{value:.1f} {unit}'
        value /= 1024


def duration(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:02}:{minutes:02}:{seconds:02}'


def snapshot():
    result = command(['systemctl', 'show', 'wb-vpn.target', 'wb-vpn-joiner.service',
                      '-p', 'Id', '-p', 'ActiveState', '-p', 'ActiveEnterTimestampMonotonic'])
    units = {}
    for block in result.stdout.strip().split('\n\n'):
        fields = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        if 'Id' in fields:
            units[fields['Id']] = fields
    try:
        data = json.loads(Path('/run/wb-vpn-status/dashboard.json').read_text())
    except (OSError, ValueError):
        data = {}
    return units, data


def logs():
    result = command(['journalctl', '-u', 'wb-vpn-joiner.service', '-u', 'wb-vpn-tun.service',
                      '-u', 'wb-vpn-guard.service', '-n', '160', '--no-pager', '-o', 'short-iso'])
    text = result.stdout or result.stderr or 'Журнал пока пуст.'
    text = re.sub(r'https?://\S+', '[ссылка скрыта]', text)
    text = re.sub(r'y0_[A-Za-z0-9_-]+', '[токен скрыт]', text)
    return text


class Dashboard(Adw.Application):
    def __init__(self):
        super().__init__(application_id='ru.local.TelemostVPN', flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.connect('activate', self.activate)
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=3)
        self.pending = set()
        self.window = None
        self.active = False
        self.busy = False
        self.tick_count = 0
        self.history = collections.deque(maxlen=90)
        self.previous = None
        self.total_rx = self.total_tx = 0
        self.log_text = ''
        self.units, self.data = {}, {}
        self.ready = read_json(PUBLIC/'setup.json', {}).get('ready', False)

    def label(self, text, css=None):
        widget = Gtk.Label(label=text, xalign=0)
        if css:
            widget.add_css_class(css)
        return widget

    def box(self, vertical=True, spacing=12):
        return Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
                       spacing=spacing)

    def card(self, title, subtitle):
        card = self.box(spacing=8)
        card.add_css_class('metric-card')
        card.set_hexpand(True)
        card.append(self.label(title, 'dim-label'))
        number = self.label('—', 'metric-number')
        card.append(number)
        detail = self.label(subtitle, 'small-note')
        detail.set_wrap(True)
        card.append(detail)
        return card, number, detail

    def activate(self, *_):
        if self.window:
            self.window.present()
            return
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        css = Gtk.CssProvider()
        css.load_from_data(b'''
            window { background: #10151f; color: #e8edf6; }
            headerbar { background: #10151f; }
            .content { padding: 22px; }
            .hero, .panel, .metric-card { background: #192231; border: 1px solid #2a3547; border-radius: 16px; padding: 18px; }
            .hero { background: #182a37; }
            .hero-title { font-size: 26px; font-weight: 800; }
            .metric-number { font-size: 25px; font-weight: 700; font-feature-settings: "tnum"; }
            .small-note { color: #98abc2; font-size: 12px; }
            .section-title { font-size: 16px; font-weight: 700; }
            .status-good { color: #65d6ba; }
            .status-wait { color: #efc17b; }
            .connect-button { padding: 12px 24px; border-radius: 12px; font-weight: 700; }
            textview, textview text { background: #111a27; color: #a8c0d9; font-family: monospace; font-size: 12px; }
            .error-message { color: #ffafa8; }
        ''')
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.window = Adw.ApplicationWindow(application=self, title='Телемост VPN', default_width=1000, default_height=850)
        root = self.box(spacing=0)
        header = Adw.HeaderBar()
        try:
            address = json.loads((PUBLIC/'server.json').read_text())['address']
        except (OSError, ValueError, KeyError):
            address = 'Выделенный сервер'
        header.set_title_widget(Adw.WindowTitle(title='Телемост VPN', subtitle='Личное соединение • ' + address))
        root.append(header)
        self.pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        switcher = Gtk.StackSwitcher(stack=self.pages, halign=Gtk.Align.CENTER)
        root.append(switcher)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        body = self.box(spacing=16)
        body.add_css_class('content')
        scroll.set_child(body)
        self.pages.add_titled(scroll, 'connection', 'Подключение')
        root.append(self.pages)
        self.window.set_content(root)

        hero = self.box(False, 24)
        hero.add_css_class('hero')
        info = self.box(spacing=8)
        info.set_hexpand(True)
        self.state = self.label('Проверяем состояние…', 'hero-title')
        self.state.set_wrap(True)
        self.subtitle = self.label('Сервер работает через CLI • транспорт Яндекс Телемост', 'small-note')
        self.subtitle.set_wrap(True)
        self.session = self.label('Время соединения —', 'small-note')
        info.append(self.state)
        info.append(self.subtitle)
        info.append(self.session)
        hero.append(info)
        self.button = Gtk.Button(label='Подключить', valign=Gtk.Align.CENTER)
        self.button.add_css_class('suggested-action')
        self.button.add_css_class('connect-button')
        self.button.connect('clicked', self.control)
        hero.append(self.button)
        body.append(hero)
        self.error = self.label('', 'error-message')
        self.error.set_wrap(True)
        self.error.set_visible(False)
        body.append(self.error)

        grid = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True)
        cards = []
        for title, subtitle in [('↓ Получение', 'Трафик VPN сейчас'), ('↑ Отправка', 'Трафик VPN сейчас'),
                                ('Отклик', 'HTTPS-запрос через VPN'), ('CPU сервера', 'Все процессы сервера')]:
            cards.append(self.card(title, subtitle))
        self.download, self.upload, self.latency, self.cpu = [card[1] for card in cards]
        for i, card in enumerate(cards):
            grid.attach(card[0], i, 0, 1, 1)
        body.append(grid)
        self.metric_grid, self.cards = grid, cards
        self.compact = False

        middle = self.box(False, 16)
        chart_panel = self.box(spacing=12)
        chart_panel.add_css_class('panel')
        chart_panel.set_hexpand(True)
        chart_panel.append(self.label('Трафик за последние 90 секунд', 'section-title'))
        self.chart = Gtk.DrawingArea(content_width=240, content_height=140, hexpand=True)
        self.chart.set_draw_func(self.draw_chart)
        chart_panel.append(self.chart)
        self.traffic_total = self.label('↓ 0 Б получено    ↑ 0 Б отправлено', 'small-note')
        chart_panel.append(self.traffic_total)
        chart_panel.append(self.label('Зелёный — получение · синий — отправка', 'small-note'))
        middle.append(chart_panel)
        server = self.box(spacing=12)
        server.add_css_class('panel')
        server.set_size_request(280, -1)
        server.append(self.label('Выделенный сервер', 'section-title'))
        self.memory = self.label('Память —', 'small-note')
        self.memory_bar = Gtk.ProgressBar()
        self.disk_label = self.label('Диск —', 'small-note')
        self.disk_bar = Gtk.ProgressBar()
        self.load_label = self.label('Нагрузка —', 'small-note')
        self.uptime = self.label('Время работы —', 'small-note')
        self.freshness = self.label('Ожидаем телеметрию', 'small-note')
        for widget in (self.memory, self.memory_bar, self.disk_label, self.disk_bar,
                       self.load_label, self.uptime, self.freshness):
            server.append(widget)
        middle.append(server)
        body.append(middle)
        self.middle, self.hero = middle, hero

        log_panel = self.box(spacing=12)
        log_panel.add_css_class('panel')
        row = self.box(False)
        title = self.label('Журнал соединения', 'section-title')
        title.set_hexpand(True)
        row.append(title)
        self.follow = Gtk.CheckButton(label='Автопрокрутка', active=True)
        row.append(self.follow)
        copy = Gtk.Button(label='Копировать')
        copy.connect('clicked', lambda *_: Gdk.Display.get_default().get_clipboard().set(self.log_text))
        row.append(copy)
        log_panel.append(row)
        self.log_view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                     wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=10, right_margin=10,
                                     top_margin=10, bottom_margin=10)
        self.log_scroll = Gtk.ScrolledWindow(min_content_height=190, vexpand=True)
        self.log_scroll.set_child(self.log_view)
        log_panel.append(self.log_scroll)
        body.append(log_panel)
        body.append(self.label('Закрытие окна оставляет VPN включённым. Для остановки используйте «Отключить».', 'small-note'))
        from preferences import Preferences
        self.preferences = Preferences(self, self.pages)
        self.pages.set_visible_child_name('connection' if self.ready else 'setup')
        self.window.present()
        GLib.timeout_add_seconds(1, self.tick)
        self.tick()

    def task(self, name, function, callback):
        if name in self.pending:
            return
        self.pending.add(name)
        future = self.pool.submit(function)
        def done():
            self.pending.discard(name)
            try:
                value = future.result()
            except Exception as exc:
                self.error.set_text(f'Не удалось обновить данные: {type(exc).__name__}')
                self.error.set_visible(True)
            else:
                callback(value)
            return False
        future.add_done_callback(lambda _: GLib.idle_add(done))

    def control(self, *_):
        if self.busy:
            return
        if not self.ready:
            self.pages.set_visible_child_name('setup')
            return
        action = 'disconnect' if self.active else 'connect'
        self.busy = True
        self.button.set_sensitive(False)
        self.button.set_label('Подождите…')
        self.error.set_visible(False)
        def finished(result):
            self.busy = False
            self.button.set_sensitive(True)
            if result.returncode:
                self.error.set_text('Действие не выполнено. Авторизация отменена или служба вернула ошибку. Подробности — в журнале.')
                self.error.set_visible(True)
            self.task('status', snapshot, self.update_status)
            self.task('logs', logs, self.update_logs)
        def perform():
            try:
                return command(['pkexec', '/usr/local/sbin/wb-vpn-control', action], timeout=180)
            except subprocess.TimeoutExpired:
                return subprocess.CompletedProcess([], 1)
        self.task('control', perform, finished)

    def update_status(self, value):
        self.units, self.data = value
        target = self.units.get('wb-vpn.target', {})
        self.active = target.get('ActiveState') in ('active', 'activating', 'deactivating')
        joined = self.units.get('wb-vpn-joiner.service', {}).get('ActiveState') == 'active'
        fresh = time.time() - self.data.get('time', 0) < 120
        state = self.data.get('state') if fresh and joined else 'connecting'
        connected = self.active and state == 'connected'
        self.state.set_text('Соединение замедлено' if self.active and state == 'degraded' else 'Соединение установлено' if connected else
                            ('Восстанавливаем соединение…' if state == 'reconnecting' else 'Подключаемся…') if self.active else 'VPN выключен')
        self.state.remove_css_class('status-good')
        self.state.remove_css_class('status-wait')
        if self.active:
            self.state.add_css_class('status-good' if connected else 'status-wait')
        self.subtitle.set_text('Трафик проходит, но контрольный запрос не успел завершиться' if self.active and state == 'degraded' else 'Телемост · проверка трафика пройдена · выход через выделенный сервер' if connected else
                               'Прямой трафик блокируется до восстановления туннеля' if self.active else
                               'Готов к подключению · автоматическое восстановление комнаты')
        if not self.busy:
            testing = Path('/run/wb-vpn-testing').exists()
            self.button.set_label('Идёт проверка…' if testing else 'Отключить' if self.active else 'Подключить')
            self.button.set_sensitive(not testing)
        entered = int(target.get('ActiveEnterTimestampMonotonic', '0')) / 1e6
        self.session.set_text('VPN включён ' + duration(time.monotonic() - entered) if self.active and entered else 'Время соединения —')
        latency = self.data.get('latency_ms')
        self.latency.set_text(f'{latency} мс' if connected and latency is not None else '—')
        metrics = self.data.get('server') or {}
        age = max(0, time.time() - metrics.get('time', 0))
        if metrics:
            cpu = metrics.get('cpu')
            self.cpu.set_text(f'{cpu:.0f} %' if cpu is not None and age < 90 and self.active else '—')
            for key, label, bar, name in [('memory', self.memory, self.memory_bar, 'Память'), ('disk', self.disk_label, self.disk_bar, 'Диск')]:
                used, total = metrics.get(key + '_used', 0), metrics.get(key + '_total', 0)
                label.set_text(f'{name}  {size(used)} / {size(total)}')
                bar.set_fraction(min(1, max(0, used / total)) if total else 0)
            self.load_label.set_text('Load 1 / 5 / 15 мин: ' + ' / '.join(f'{x:.2f}' for x in metrics.get('load', [])))
            self.uptime.set_text(f"Работает {int(metrics.get('uptime', 0) // 86400)} дн. · CPU: {metrics.get('cores', '—')}")
            self.freshness.set_text(f'Обновлено {int(age)} с назад' if age < 90 and self.active else 'Последние данные · сейчас не обновляются')
        else:
            self.cpu.set_text('—')
            self.freshness.set_text('Ожидаем телеметрию' if self.active else 'Телеметрия доступна после подключения')

    def update_logs(self, value):
        if value == self.log_text:
            return
        self.log_text = value
        self.log_view.get_buffer().set_text(value)
        if self.follow.get_active():
            GLib.idle_add(self.scroll_logs)

    def scroll_logs(self):
        adjustment = self.log_scroll.get_vadjustment()
        adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        return False

    def tick(self):
        self.tick_count += 1
        compact = self.window.get_width() < 820
        if compact != self.compact:
            self.compact = compact
            for card in self.cards:
                self.metric_grid.remove(card[0])
            for i, card in enumerate(self.cards):
                self.metric_grid.attach(card[0], i % 2 if compact else i, i // 2 if compact else 0, 1, 1)
            self.middle.set_orientation(Gtk.Orientation.VERTICAL if compact else Gtk.Orientation.HORIZONTAL)
        self.hero.set_orientation(Gtk.Orientation.VERTICAL if self.window.get_width() < 620 else Gtk.Orientation.HORIZONTAL)
        self.task('status', snapshot, self.update_status)
        if self.tick_count % 4 == 1:
            self.task('logs', logs, self.update_logs)
        now = time.monotonic()
        try:
            # Kernel TUN tx is traffic sent into userspace proxy (upload).
            base = Path('/sys/class/net/wb-tun/statistics')
            rx, tx = (int((base / (key + '_bytes')).read_text()) for key in ('rx', 'tx'))
            down = up = 0
            if self.previous:
                previous_time, previous_rx, previous_tx = self.previous
                elapsed = max(.01, now - previous_time)
                down, up = max(0, rx - previous_rx) / elapsed, max(0, tx - previous_tx) / elapsed
            self.previous = now, rx, tx
            self.total_rx, self.total_tx = rx, tx
        except OSError:
            self.previous = None
            down = up = 0
        self.download.set_text(size(down) + '/с')
        self.upload.set_text(size(up) + '/с')
        self.traffic_total.set_text(f'↓ {size(self.total_rx)} получено    ↑ {size(self.total_tx)} отправлено')
        self.history.append((down, up))
        self.chart.queue_draw()
        return True

    def draw_chart(self, area, ctx, width, height):
        samples = list(self.history)
        peak = max(1024, max((max(pair) for pair in samples), default=0))
        ctx.set_source_rgb(.18, .23, .30)
        ctx.set_line_width(1)
        for row in range(1, 4):
            y = height * row / 4
            ctx.move_to(0, y)
            ctx.line_to(width, y)
        ctx.stroke()
        for index, color in [(0, (.39, .84, .73)), (1, (.43, .64, .98))]:
            ctx.set_source_rgb(*color)
            ctx.set_line_width(2)
            for i, pair in enumerate(samples):
                x = width * (90 - len(samples) + i) / 89
                y = height - 8 - pair[index] / peak * (height - 26)
                if i == 0:
                    ctx.move_to(x, y)
                else:
                    ctx.line_to(x, y)
            ctx.stroke()
        ctx.set_source_rgb(.60, .67, .76)
        ctx.set_font_size(11)
        ctx.move_to(4, 12)
        ctx.show_text(size(peak) + '/s')


if __name__ == '__main__':
    Dashboard().run()
