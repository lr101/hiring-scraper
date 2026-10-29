import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cvFormattingPrompt, cvFormattingPromptDe } from '../../extensions/application-companion/tex-cv-prompt.js';

test('both CV formatting prompts use the same generic photo filename', () => {
  for (const prompt of [cvFormattingPrompt, cvFormattingPromptDe]) {
    assert.match(prompt, /\\includegraphics\{portrait\.png\}/);
  }
});
