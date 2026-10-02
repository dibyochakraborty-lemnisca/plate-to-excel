"""Run against a disposable lab server: LAB_TEST_URL=http://127.0.0.1:8510 python test_browser.py."""
import os
from io import BytesIO
from uuid import uuid4

from PIL import Image
from playwright.sync_api import sync_playwright, expect

url = os.environ.get('LAB_TEST_URL', 'http://127.0.0.1:8510')
with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    context = browser.new_context(viewport={'width':390,'height':844})
    page = context.new_page()
    page.goto(url)
    page.get_by_role('button', name='New experiment', exact=True).click()
    page.get_by_label('Experiment ID', exact=True).fill('BROWSER-TEST-' + str(uuid4())[:8])
    page.get_by_role('button', name='Save experiment', exact=True).click()
    page.get_by_label('Template name', exact=True).fill('Flasks 01–08')
    page.get_by_role('button', name='Save template', exact=True).click()
    expect(page.locator('#capture-template')).not_to_have_value('')
    assert page.locator('#auth').count() == 0
    page.evaluate('navigator.serviceWorker.ready')
    page.reload()
    expect(page.locator('#capture-template')).not_to_have_value('')
    context.set_offline(True)
    image = BytesIO()
    Image.new('RGB',(20,20),'white').save(image, 'JPEG')
    for name in ('offline-one.jpg','offline-two.jpg'):
        page.locator('#camera').set_input_files({'name':name,'mimeType':'image/jpeg','buffer':image.getvalue()})
    expect(page.locator('[data-remove-local]')).to_have_count(2)
    page.reload()
    expect(page.locator('[data-remove-local]')).to_have_count(2)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    context.set_offline(False)
    expect(page.locator('#capture-list article')).to_have_count(2, timeout=30000)
    expect(page.locator('[data-remove-local]')).to_have_count(0)
    page.reload()
    expect(page.locator('#capture-list article')).to_have_count(2)
    page.get_by_role('button',name='Setup',exact=True).click()
    page.get_by_role('button',name='Duplicate',exact=True).click()
    page.get_by_label('Template name',exact=True).fill('Flasks 09–16')
    page.get_by_label('First flask number',exact=True).fill('9')
    page.get_by_role('button',name='Save template',exact=True).click()
    expect(page.locator('#capture-template option')).to_have_count(2)
    page.set_viewport_size({'width':1440,'height':1000})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    browser.close()
print('Browser checks passed: setup, no login, two offline captures, offline reload, reconnect upload, persistence, template reuse, mobile/desktop overflow.')
