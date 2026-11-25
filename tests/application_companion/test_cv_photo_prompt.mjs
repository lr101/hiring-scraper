import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cvFormattingPrompt, cvFormattingPromptDe } from '../../extensions/application-companion/tex-cv-prompt.js';

test('both CV formatting prompts use the same generic photo filename', () => {
  for (const prompt of [cvFormattingPrompt, cvFormattingPromptDe]) {
    assert.match(prompt, /\\includegraphics\{portrait\.png\}/);
  }
});

test('documented formatting prompt uses the same portrait filename', async () => {
  const { readFile } = await import('node:fs/promises');
  const documentation = await readFile(new URL('../../docs/cv-formatting-prompt.md', import.meta.url), 'utf8');
  const prompt = documentation.split('```text\n')[1].split('```')[0];
  assert.match(prompt, /\\includegraphics\{portrait\.png\}/);
});
