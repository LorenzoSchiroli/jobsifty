import client from './client'

export interface CVResponse {
  cv_text: string | null
  has_cv: boolean
}

export const getCV = () =>
  client.get<CVResponse>('/users/cv').then(r => r.data)

export const extractCV = (file: File) => {
  const form = new FormData()
  form.append('file', file)
  return client.post<{ text: string }>('/users/cv/extract', form).then(r => r.data)
}

export const redactCV = (text: string) =>
  client.post<{ text: string; removed: Record<string, number> }>('/users/cv/redact', { text }).then(r => r.data)

export const saveCV = (text: string, confirm = false) =>
  client.put<CVResponse>('/users/cv', { text, confirm }).then(r => r.data)
