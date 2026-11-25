export function currentJob(catalog, selectedId = null) {
  const exact = (catalog?.items ?? []).filter((item) => item.exact);
  if (selectedId)
    return exact.find((item) => item.job.id === selectedId) ?? null;
  return exact.length === 1 ? exact[0] : null;
}
