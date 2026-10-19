"""Navigate secondary controls in the focused application flow."""
def application_action(page, name):
    action = page.get_by_role('button', name=name, exact=True)
    if not action.is_visible():
        page.get_by_text('Options', exact=True).click()
    action.click()
