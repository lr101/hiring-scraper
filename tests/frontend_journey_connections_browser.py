"""Journey connectors keep every branch attached through resizing and scrolling."""
import os
from playwright.sync_api import sync_playwright, expect

web = os.environ['APPLICATION_TEST_WEB_URL'].rstrip('/')
with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), args=['--no-sandbox'])
    page = browser.new_page(viewport={'width':1440,'height':1000}, reduced_motion='reduce')
    errors=[]
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(f'{web}/?profile_id=1#quest-map')
    expect(page.get_by_role('button', name='All roles', exact=False)).to_be_visible(timeout=15000)
    paths=page.locator('svg.journey-connections path[data-from]')
    expect(paths).to_have_count(10)
    expected={('new','open'),('new','in_gots_hand'),('open','in_gots_hand'),
              ('open','waiting_for_reply'),('open','automated_application'),
              ('in_gots_hand','waiting_for_reply'),('in_gots_hand','automated_application'),
              ('waiting_for_reply','interview'),('automated_application','interview'),('interview','accepted')}
    endpoints_attached = '''() => [...document.querySelectorAll('svg.journey-connections path[data-from]')].every(path => {
                const matrix=path.getScreenCTM();
                return ['from','to'].every((key,index) => {
                    const point=path.getPointAtLength(index ? path.getTotalLength() : 0);
                    const screen=new DOMPoint(point.x,point.y).matrixTransform(matrix);
                    const box=document.querySelector(`[data-journey-stage="${path.dataset[key]}"]`).getBoundingClientRect();
                    return screen.x >= box.left-.5 && screen.x <= box.right+.5 && screen.y >= box.top-.5 && screen.y <= box.bottom+.5;
                });
            })'''
    for theme in ['light','dark']:
        page.evaluate('(theme) => document.documentElement.dataset.theme=theme', theme)
        for width in [1440,768,390,320,1440]:
            page.set_viewport_size({'width':width,'height':1000})
            # Real SVG endpoints must touch their own buttons, including mobile
            # scroll content and a later resize back to a wide desktop.
            page.wait_for_function(endpoints_attached)
            edges=paths.evaluate_all('(els) => els.map(el => [el.dataset.from,el.dataset.to])')
            assert set(map(tuple,edges)) == expected
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (theme,width)
            page.wait_for_function('''() => {
                const el=document.querySelector('.quest-trail');
                return Math.abs(el.scrollWidth - Math.max(el.clientWidth,...[...el.querySelectorAll('.quest-stage')].map(b => b.offsetLeft+b.offsetWidth+parseFloat(getComputedStyle(el).paddingRight)))) < 3;
            }''')
            page.locator('.quest-trail').evaluate('el => el.scrollLeft=el.scrollWidth')
            page.wait_for_function(endpoints_attached)
            # Selecting a source must not detach its vertical branch; the SVG
            # layer also must not intercept any stage button.
            for name in ['Interested,','Accepted,']:
                target=page.get_by_role('button', name=name,exact=False)
                target.click()
                expect(target).to_have_attribute('aria-pressed','true')
                page.wait_for_function(endpoints_attached)
    assert not errors, errors
    print('Ten branch/merge paths remain attached, clickable and overflow-free across light/dark, resize and mobile scroll')
    browser.close()
