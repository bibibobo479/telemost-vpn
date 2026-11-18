"""Setup wizard and transport settings, embedded in the dashboard."""
import json
from pathlib import Path
import subprocess
import threading
import uuid
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Gtk, GLib, Gio
from appcore import DEFAULT_CLIENT_ID, DEFAULT_PROFILE, PUBLIC, USER_CONFIG, cookies, oauth_url, profile, read_json, server
from browser_auth import BrowserLogin
from setup_backend import Job, SSH, manager, provision, reuse_server


class Preferences:
    def __init__(self, app, stack):
        self.app, self.stack = app, stack
        self.browser = BrowserLogin()
        self.job = None
        self.cookie_data = None
        self.namespace = 'disk:/Телемост VPN/'+uuid.uuid4().hex
        self.checked_server = None
        self.stage = 0
        self.setup = self.page()
        stack.add_titled(self.setup,'setup','Настройка сервера')
        self.settings = self.page()
        stack.add_titled(self.settings,'settings','Настройки')
        self.make_wizard(self.setup.body)
        self.make_settings(self.settings.body)
        app.window.connect('close-request',self.on_close)

    def page(self):
        scroll = Gtk.ScrolledWindow(vexpand=True,hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.body = self.app.box(spacing=16)
        scroll.body.add_css_class('content')
        scroll.set_child(scroll.body)
        return scroll

    def note(self, text, css='small-note'):
        label = self.app.label(text,css)
        label.set_wrap(True)
        label.set_selectable(True)
        return label

    def field(self, box, title, secret=False, text=''):
        box.append(self.note(title))
        entry = Gtk.PasswordEntry(show_peek_icon=True) if secret else Gtk.Entry()
        entry.set_text(text)
        entry.set_hexpand(True)
        box.append(entry)
        return entry

    def button(self, box, title, callback, primary=False):
        button = Gtk.Button(label=title,halign=Gtk.Align.START)
        if primary:
            button.add_css_class('suggested-action')
        button.connect('clicked',callback)
        box.append(button)
        return button

    def make_wizard(self, body):
        body.append(self.note('Ваш личный сервер за несколько шагов','hero-title'))
        body.append(self.note('Нужен VPS с Ubuntu/Debian x86‑64 и доступом root по SSH. Вход в Яндекс выполняется на этом компьютере.'))
        self.steps_label = self.note('1 · Сервер    →    2 · Яндекс    →    3 · Установка    →    4 · Готово')
        body.append(self.steps_label)
        self.steps = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        body.append(self.steps)
        self.server_page = self.app.box()
        self.server_page.append(self.note('Есть код подключения? Сервер и вход в Яндекс повторно настраивать не нужно.'))
        self.import_code = self.field(self.server_page,'Код подключения',secret=True)
        self.import_button = self.button(self.server_page,'Подключить по коду',self.import_shared,True)
        old = read_json(PUBLIC/'server.json',{})
        self.address = self.field(self.server_page,'IP сервера',text=old.get('address',''))
        self.port = Gtk.SpinButton.new_with_range(1,65535,1)
        self.port.set_value(old.get('port',22))
        self.server_page.append(self.note('Порт SSH'))
        self.server_page.append(self.port)
        self.password = self.field(self.server_page,'Пароль root — используется только во время настройки',secret=True)
        self.server_page.append(self.note('Первый ключ SSH запоминается. При изменении ключа подключение будет отклонено.'))
        self.check_button = self.button(self.server_page,'Проверить сервер и продолжить',self.check_server,True)
        self.steps.add_named(self.server_page,'server')
        existing = self.app.box()
        existing.append(self.note('Сервер уже настроен','section-title'))
        existing.append(self.note('Используем установленную службу и её авторизацию Яндекса. Повторная установка не требуется.'))
        self.reuse_button = self.button(existing,'Использовать этот сервер',self.reuse_existing,True)
        self.button(existing,'Назад',lambda *_:self.steps.set_visible_child_name('server'))
        self.steps.add_named(existing,'existing')

        self.yandex_page = self.app.box()
        self.yandex_page.append(self.note('Разрешите обмен данными через Яндекс Диск','section-title'))
        self.yandex_page.append(self.note('Нажмите «Получить токен», войдите в Яндекс, разрешите доступ и вставьте показанный OAuth-токен. Нужны права чтения и записи Диска.'))
        self.client_id = self.field(self.yandex_page,'Client ID OAuth-приложения',text=DEFAULT_CLIENT_ID)
        self.button(self.yandex_page,'Получить токен в браузере',self.open_oauth)
        self.button(self.yandex_page,'Создать своё OAuth-приложение',lambda *_:self.open_url('https://oauth.yandex.ru/client/new'))
        self.yandex_page.append(self.note('Если Яндекс отклоняет доступ к общему приложению, создайте своё: Redirect URI https://oauth.yandex.ru/verification_code; права cloud_api:disk.read и cloud_api:disk.write. Вставьте его Client ID выше. Client Secret не нужен.'))
        self.token = self.field(self.yandex_page,'OAuth-токен',secret=True)
        self.yandex_page.append(self.note('Войдите в Яндекс Телемост','section-title'))
        self.yandex_page.append(self.note('Откроется отдельное окно Chrome/Chromium. Войдите в аккаунт и вернитесь сюда. Приложение передаст cookies этого аккаунта на ваш VPS для создания комнаты.'))
        self.login_button = self.button(self.yandex_page,'Открыть вход в Яндекс',self.open_login)
        self.collect_button = self.button(self.yandex_page,'Я вошёл — получить cookies',self.collect_cookies)
        self.button(self.yandex_page,'Импортировать cookies из JSON',self.import_cookies)
        self.cookie_status = self.note('Cookies ещё не получены')
        self.yandex_page.append(self.cookie_status)
        self.install_button = self.button(self.yandex_page,'Настроить сервер автоматически',self.install,True)
        self.button(self.yandex_page,'Назад к серверу',lambda *_:self.steps.set_visible_child_name('server'))
        self.steps.add_named(self.yandex_page,'yandex')

        running = self.app.box()
        running.append(self.note('Устанавливаем и проверяем соединение','section-title'))
        running.append(self.note('Пароль SSH не сохраняется. Токен и cookies записываются только в закрытые файлы на вашем сервере; токен также нужен клиенту для автоматического подключения.'))
        self.steps.add_named(running,'installing')
        done = self.app.box()
        done.append(self.note('Сервер готов','hero-title'))
        done.append(self.note('Комната создана, обмен данными работает. Нажмите «Подключиться», чтобы включить VPN на этом компьютере.'))
        self.button(done,'Подключиться',self.connect_ready,True)
        self.steps.add_named(done,'done')
        self.setup_status = self.note('','section-title')
        body.append(self.setup_status)
        self.setup_log = Gtk.TextView(editable=False,cursor_visible=False,wrap_mode=Gtk.WrapMode.WORD_CHAR)
        pane = Gtk.ScrolledWindow(min_content_height=150)
        pane.set_child(self.setup_log)
        body.append(pane)
        self.cancel_setup = self.button(body,'Отменить операцию',self.cancel)
        self.cancel_setup.set_sensitive(False)

    def make_settings(self, body):
        body.append(self.note('Скорость и транспорт','hero-title'))
        body.append(self.note('FPS и batch задают темп передачи. При подключении клиент отправляет их серверу. Более высокие значения не всегда дают большую скорость.'))
        self.fps = Gtk.SpinButton.new_with_range(1,240,1)
        self.batch = Gtk.SpinButton.new_with_range(1,256,1)
        for title,widget in [('FPS — частота передачи',self.fps),('Batch — множитель пакетов',self.batch)]:
            body.append(self.note(title,'section-title'))
            body.append(widget)
        self.dual = Gtk.Switch(halign=Gtk.Align.START)
        body.append(self.note('Dual-track — второй видеоканал','section-title'))
        body.append(self.dual)
        body.append(self.note('Это два видеоканала одного соединения, а не два пользователя. При переключении приложение обновит комнату. Автоподбор выбирает по скорости, ошибки передачи не исключают профиль.'))
        self.apply_button = self.button(body,'Сохранить и применить',self.apply,True)
        self.load_profile()
        body.append(self.note('Автоподстройка','section-title'))
        body.append(self.note('Проверим несколько сочетаний FPS/batch и один/два канала, затем повторно проверим лучший. Обычно 5–12 минут, до 100 МБ трафика. Телемост VPN временно отключится и восстановится после теста; Amnezia не изменяется.'))
        self.tune_button = self.button(body,'Подобрать оптимальные настройки',self.autotune,True)
        self.progress = Gtk.ProgressBar(show_text=True)
        body.append(self.progress)
        self.settings_status = self.note('')
        body.append(self.settings_status)
        self.result_box = self.app.box(spacing=8)
        body.append(self.result_box)
        self.cancel_tune = self.button(body,'Остановить и сохранить прежние настройки',self.cancel)
        self.cancel_tune.set_sensitive(False)
        self.button(body,'Настроить другой сервер',lambda *_:self.stack.set_visible_child_name('setup'))
        body.append(self.note('Поделиться подключением','section-title'))
        body.append(self.note('Код содержит секретный токен Диска. Передавайте его только доверенному человеку. Пароль SSH и cookies в код не входят. Это одна сессия: перед подключением другого компьютера отключите текущий.'))
        self.share_button = self.button(body,'Создать код подключения',self.export_shared)
        self.share_code = self.field(body,'Код для другого компьютера',secret=True)
        self.share_code.set_editable(False)
        self.button(body,'Копировать код',self.copy_shared)
        report = read_json(PUBLIC/'autotune.json')
        if report:
            for result in report.get('results',[]):
                self.measurement(result)

    def current_profile(self):
        return profile({'fps':self.fps.get_value_as_int(),'batch':self.batch.get_value_as_int(),'dual_track':self.dual.get_active()})

    def load_profile(self, value=None):
        value = value or read_json(PUBLIC/'transport.json',DEFAULT_PROFILE)
        self.fps.set_value(value['fps'])
        self.batch.set_value(value['batch'])
        self.dual.set_active(value['dual_track'])

    def open_url(self,url):
        Gio.AppInfo.launch_default_for_uri(url,None)

    def open_oauth(self,*_):
        try:
            self.open_url(oauth_url(self.client_id.get_text().strip()))
        except Exception as exc:
            self.setup_status.set_text(str(exc))

    def run_job(self, title, function, success=None, setup=False):
        if self.job:
            return
        self.app.busy = True
        self.job = Job(lambda event:GLib.idle_add(self.event,event,setup))
        job = self.job
        self.event({'message':title},setup)
        for button in (self.check_button,self.install_button,self.apply_button,self.tune_button,self.login_button,self.collect_button,self.import_button,self.reuse_button,self.share_button):
            button.set_sensitive(False)
        self.cancel_setup.set_sensitive(setup)
        self.cancel_tune.set_sensitive(not setup)
        self.app.button.set_sensitive(False)
        def work():
            try:
                result = function(job)
                error = None
            except Exception as exc:
                result = None
                # Never display token/cookie payloads or raw remote command output.
                error = str(exc) if isinstance(exc,(ValueError,InterruptedError)) else 'Операция не завершена: '+type(exc).__name__
            finally:
                job.close()
            GLib.idle_add(finish,result,error)
        def finish(result,error):
            self.job = None
            self.app.busy = False
            for button in (self.check_button,self.install_button,self.apply_button,self.tune_button,self.login_button,self.collect_button,self.import_button,self.reuse_button,self.share_button):
                button.set_sensitive(True)
            self.cancel_setup.set_sensitive(False)
            self.cancel_tune.set_sensitive(False)
            if error:
                self.event({'message':error},setup)
                if setup and self.steps.get_visible_child_name() == 'installing':
                    self.steps.set_visible_child_name('yandex')
            elif success:
                success(result)
            self.app.task('status',__import__('dashboard').snapshot,self.app.update_status)
            return False
        threading.Thread(target=work,daemon=True).start()

    def event(self,event,setup=False):
        message = event.get('message','')
        (self.setup_status if setup else self.settings_status).set_text(message)
        if setup:
            buffer = self.setup_log.get_buffer()
            buffer.insert(buffer.get_end_iter(),message+'\n')
        if 'progress' in event:
            self.progress.set_fraction(event['progress'])
            self.progress.set_text(f"{int(event['progress']*100)} %")
        if event.get('type') == 'measurement':
            self.measurement(event['measurement'])
        return False

    def measurement(self,row):
        value = row['profile']
        name = f"{value['fps']} FPS · batch {value['batch']} · {'2 канала' if value['dual_track'] else '1 канал'}"
        text = f"{row.get('single',0)*8/1e6:.2f} Мбит/с одна загрузка · {row.get('aggregate',0)*8/1e6:.2f} Мбит/с суммарно"
        if not row.get('ok'):
            text += ' · Есть ошибки передачи (не влияют на выбор)'
        self.result_box.append(self.note(name+'\n'+text))

    def check_server(self,*_):
        try:
            address = server({'address':self.address.get_text(),'port':self.port.get_value_as_int()})
            password = self.password.get_text()
            if not password:
                raise ValueError('Введите пароль root')
        except ValueError as exc:
            self.setup_status.set_text(str(exc))
            return
        def done(result):
            self.checked_server = address
            self.setup_status.set_text('Сервер доступен: '+result.get('os','Linux'))
            self.steps.set_visible_child_name('existing' if result.get('configured') else 'yandex')
        self.run_job('Проверяем SSH…',lambda job:SSH(address,password).probe(),done,setup=True)

    def connection_ready(self,result):
        self.password.set_text('')
        self.import_code.set_text('')
        self.app.ready = True
        self.load_profile()
        self.steps.set_visible_child_name('done')
        self.setup_status.set_text(result.get('message','Подключение готово'))

    def reuse_existing(self,*_):
        address = self.checked_server
        password = self.password.get_text()
        selected = self.current_profile()
        self.run_job('Подключаем существующий сервер…',lambda job:reuse_server(address,password,selected,job),self.connection_ready,setup=True)

    def import_shared(self,*_):
        from appcore import decode_share
        code = self.import_code.get_text().strip()
        try:
            decode_share(code)
        except ValueError as exc:
            self.setup_status.set_text(str(exc))
            return
        self.run_job('Проверяем код и комнату…',lambda job:manager('import',{'code':code},job),self.connection_ready,setup=True)

    def export_shared(self,*_):
        if not self.app.ready:
            self.stack.set_visible_child_name('setup')
            return
        self.run_job('Создаём код…',lambda job:manager('export',{},job),lambda result:self.share_code.set_text(result['code']))

    def copy_shared(self,*_):
        code = self.share_code.get_text()
        if code:
            self.app.window.get_clipboard().set(code)
            self.settings_status.set_text('Код скопирован. На другом компьютере вставьте его в мастере настройки')

    def open_login(self,*_):
        try:
            self.browser.open()
            self.cookie_status.set_text('Войдите в отдельном окне и нажмите «Я вошёл»')
        except Exception as exc:
            self.cookie_status.set_text(str(exc))

    def collect_cookies(self,*_):
        def done(value):
            self.cookie_data = value
            self.cookie_status.set_text('Cookies получены. Можно настраивать сервер')
        self.run_job('Получаем cookies из окна входа…',lambda job:self.browser.collect(),done,setup=True)

    def import_cookies(self,*_):
        chooser = Gtk.FileChooserNative(title='Cookies Яндекса в JSON',transient_for=self.app.window,
                                        action=Gtk.FileChooserAction.OPEN,accept_label='Импортировать',cancel_label='Отмена')
        def response(dialog,code):
            if code == Gtk.ResponseType.ACCEPT:
                try:
                    path = Path(dialog.get_file().get_path())
                    if path.stat().st_size > 1024*1024:
                        raise ValueError('Файл cookies слишком большой')
                    self.cookie_data = cookies(json.loads(path.read_text()))
                    self.cookie_status.set_text('Cookies импортированы')
                except Exception as exc:
                    self.cookie_status.set_text(str(exc) if isinstance(exc,ValueError) else 'Не удалось прочитать JSON cookies')
            dialog.destroy()
        chooser.connect('response',response)
        chooser.show()

    def install(self,*_):
        try:
            address = server({'address':self.address.get_text(),'port':self.port.get_value_as_int()})
            if address != self.checked_server:
                raise ValueError('Сначала проверьте доступ к этому серверу')
            token = self.token.get_text().strip()
            from appcore import Disk
            Disk(token,self.namespace)
            cookie_data = cookies(self.cookie_data)
            password = self.password.get_text()
            selected = self.current_profile()
        except ValueError as exc:
            self.setup_status.set_text(str(exc))
            return
        self.steps.set_visible_child_name('installing')
        def done(result):
            self.password.set_text('')
            self.token.set_text('')
            self.cookie_data = None
            self.browser.close()
            self.steps.set_visible_child_name('done')
            self.app.ready = True
            self.setup_status.set_text(result.get('message','Готово'))
        self.run_job('Начинаем установку…',lambda job:provision(address,password,token,cookie_data,selected,self.namespace,job),done,setup=True)

    def connect_ready(self,*_):
        self.stack.set_visible_child_name('connection')
        if not self.app.active:
            self.app.control()

    def apply(self,*_):
        if not read_json(PUBLIC/'setup.json',{}).get('ready'):
            self.stack.set_visible_child_name('setup')
            return
        selected = self.current_profile()
        self.run_job('Применяем профиль…',lambda job:manager('settings',{'profile':selected},job),lambda result:self.load_profile(result['profile']))

    def autotune(self,*_):
        if not read_json(PUBLIC/'setup.json',{}).get('ready'):
            self.stack.set_visible_child_name('setup')
            return
        child = self.result_box.get_first_child()
        while child:
            following = child.get_next_sibling()
            self.result_box.remove(child)
            child = following
        def done(result):
            self.load_profile(result['profile'])
            self.progress.set_fraction(1)
            self.progress.set_text('Готово')
        self.run_job('Запускаем автоподбор…',lambda job:manager('tune',{},job),done)

    def cancel(self,*_):
        if self.job:
            self.job.cancel()
            self.setup_status.set_text('Отмена запрошена. Завершаем текущий шаг и восстанавливаем подключение…')
            self.settings_status.set_text('Отмена запрошена. Восстанавливаем прежние настройки…')

    def on_close(self,*_):
        if self.job:
            self.cancel()
            return True
        self.browser.close()
        return False
