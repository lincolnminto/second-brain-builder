import { expect, it } from 'vitest';
import { stripLeadingDuplicateH1 } from './markdown-body';

it('strips a duplicate title H1 with case and whitespace differences', () => {
  expect(stripLeadingDuplicateH1('#  heron \n\nBody.', 'Heron')).toBe('Body.');
  expect(stripLeadingDuplicateH1('\n\n# HERON\n\nBody.', 'heron')).toBe('Body.');
  expect(stripLeadingDuplicateH1('# Other\n\nBody.', 'Heron')).toBe('# Other\n\nBody.');
  expect(stripLeadingDuplicateH1('## Not title\n\nBody.', 'Heron')).toBe('## Not title\n\nBody.');
});