export interface HealthData {
  status: string
  service: string
}

export interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

export type HealthResponse = ApiResponse<HealthData>
