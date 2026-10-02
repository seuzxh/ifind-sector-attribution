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
    // 保留结构化接口错误供页面读取，既有页面仍使用原来的 Error.message。
    return Promise.reject(Object.assign(new Error(msg), { response: error?.response }))
  },
)

export default http
