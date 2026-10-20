export type CVExperience = { id: string; company: string; title: string; location: string; start: string; end: string | null; current: boolean; description: string; cv_bullet_ids: number[] }
export function parseTexCV(source: string): { experiences: CVExperience[]; education: CVExperience[] }
export function selectCVLanguage(language?: string, posting?: string): 'en' | 'de'
export function withCVLanguage<T extends { base_cvs?: Partial<Record<'en' | 'de', string>> }>(profile: T, language?: string): T & { cv_text?: string; experiences?: CVExperience[]; education?: CVExperience[] }
export function validateTailoredCV(original: string, tailored?: string): ReturnType<typeof parseTexCV>
