import type { Profile } from '../../frontend/src/profile'
export const localizedKeys: readonly string[]
export const localizedPreferenceKeys: readonly string[]
export function profileForLanguage<T extends Partial<Profile> | null | undefined>(profile: T, language?: string): T
