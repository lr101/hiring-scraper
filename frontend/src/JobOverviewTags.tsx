type JobOverview = {
  salary?: string | null
  employment_type?: string | null
  schedule?: string | null
  seniority?: string | null
  work_arrangement?: string | null
  is_remote?: boolean
  enrichment?: { experience_years: { value: number } | null }
}

function cleanValue(value: string | null | undefined) {
  return value?.trim().replaceAll('_', ' ') || ''
}

function titleCase(value: string) {
  return value.replace(/\b\w/g, character => character.toUpperCase())
}

export default function JobOverviewTags({ job }: { job: JobOverview }) {
  const salary = cleanValue(job.salary)
  const arrangementValue = cleanValue(job.work_arrangement)
  const arrangementKey = arrangementValue.toLowerCase().replace(/[\s-]+/g, '_')
  const arrangement = job.is_remote || arrangementKey === 'remote'
    ? 'Remote'
    : arrangementKey === 'hybrid'
      ? 'Hybrid'
      : arrangementKey === 'onsite' || arrangementKey === 'on_site'
        ? 'On-site'
        : titleCase(arrangementValue)
  const minimumExperience = job.enrichment?.experience_years?.value
  const experienceLabel = minimumExperience == null ? '' :
    `Experience · ${minimumExperience}+ ${minimumExperience === 1 ? 'year' : 'years'}`
  const tags = [
    salary ? { key: 'salary', label: `Salary · ${salary}`, kind: 'salary' } : null,
    cleanValue(job.employment_type) ? { key: 'employment-type', label: titleCase(cleanValue(job.employment_type)), kind: '' } : null,
    cleanValue(job.schedule) ? { key: 'schedule', label: titleCase(cleanValue(job.schedule)), kind: '' } : null,
    cleanValue(job.seniority) ? { key: 'seniority', label: titleCase(cleanValue(job.seniority)), kind: '' } : null,
    experienceLabel ? { key: 'experience', label: experienceLabel, kind: '' } : null,
    arrangement ? { key: 'work-arrangement', label: arrangement, kind: '' } : null,
  ].filter((tag): tag is { key: string; label: string; kind: string } => tag !== null)

  if (!tags.length) return null

  return <div className="job-overview-tags" aria-label="Job overview">
    {tags.map(tag => <span className={`job-overview-tag ${tag.kind ? `job-overview-tag-${tag.kind}` : ''}`} key={tag.key}>{tag.label}</span>)}
  </div>
}
