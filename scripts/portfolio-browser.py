from pathlib import Path
import shutil
import time
import uuid
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright, expect

base='https://axyro.tech/vidharbor/'
title='Проверка выкладки '+uuid.uuid4().hex[:10]
with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path=shutil.which('google-chrome') or shutil.which('chromium'),args=['--no-sandbox','--disable-dev-shm-usage'])
    owner=browser.new_context()
    page=owner.new_page()
    page.set_default_timeout(12000)
    page.goto(base,wait_until='domcontentloaded')
    expect(page.locator('#catalog-title')).to_be_visible()
    expect(page.get_by_label('Оформление')).to_have_value('classic')
    page.get_by_role('link',name='Творческая студия',exact=False).click()
    expect(page.get_by_label('Название видео')).to_be_visible()
    try:
        page.get_by_label('Название видео').fill(title)
        page.get_by_label('Видеофайл').set_input_files(str(Path(__file__).with_name('portfolio-fixture.mp4')))
        page.get_by_role('button',name='Загрузить',exact=True).click()
        link=page.get_by_role('link',name='Готово · Смотреть')
        expect(link).to_be_visible(timeout=90000)
        watch=urljoin(base,link.get_attribute('href'))
        viewer=browser.new_context()
        public=viewer.new_page()
        public.goto(watch,wait_until='domcontentloaded')
        expect(public.locator('#watch-title')).to_have_text(title)
        public.locator('video.player').evaluate('(v)=>{v.muted=true;return v.play()}')
        player = public.locator('video.player')
        end = time.monotonic() + 18
        while time.monotonic() < end:
            if player.evaluate('(video) => video.currentTime') > 0.5:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError('Video did not advance beyond 0.5 seconds')
        print('Guest upload and anonymous HLS playback verified')
    finally:
        page.goto(base+'studio',wait_until='domcontentloaded')
        item=page.get_by_role('listitem').filter(has_text=title)
        if item.count()==0:
            try:
                item.wait_for(timeout=5000)
            except Exception:
                pass
        if item.count()==1:
            page.once('dialog',lambda dialog:dialog.accept())
            item.get_by_role('button',name='Удалить',exact=True).click()
            expect(item).to_have_count(0)
            print('Test video removed')
    browser.close()
