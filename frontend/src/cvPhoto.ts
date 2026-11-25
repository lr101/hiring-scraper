export async function readCVPhoto(file: File): Promise<string> {
  if (file.type !== 'image/png' || file.size > 2 * 1024 * 1024) throw new Error('Choose a PNG photo under 2 MiB.')
  const bitmap = await createImageBitmap(file).catch(() => { throw new Error('Could not read this PNG photo.') })
  const valid = bitmap.width <= 4096 && bitmap.height <= 4096
  bitmap.close()
  if (!valid) throw new Error('Use a PNG photo no larger than 4096 pixels per side.')
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('Could not read this PNG photo.'))
    reader.onload = () => resolve(reader.result as string)
    reader.readAsDataURL(file)
  })
}
