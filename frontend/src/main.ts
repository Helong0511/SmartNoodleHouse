import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './styles/global.css'
import App from './App.vue'
import router from './router'
import { installFrontendLogger, logger } from '@/utils/logger'

installFrontendLogger()

const app = createApp(App)
app.config.errorHandler = (err, _instance, info) => {
  logger.error(`vue: ${info || 'error'}`, err)
}
app.use(createPinia())
app.use(router)
app.use(ElementPlus)
app.mount('#app')
