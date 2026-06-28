import axios from 'axios'

// 统一 axios 实例：baseUrl 留空走相对路径（dev 经 vite proxy，prod 同源）
const http = axios.create({
  timeout: 60000,
  headers: { 'Content-Type': 'application/json' },
})

// 统一错误处理：返回后端 error 字段或抛出
http.interceptors.response.use(
  (resp) => resp.data,
  (error) => {
    const msg = error?.response?.data?.detail || error?.message || '请求失败'
    return Promise.reject(new Error(msg))
  },
)

export default http
