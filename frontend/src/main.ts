import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import './styles/global.css'
import './styles/theme.css'
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
app.use(ElementPlus, { locale: zhCn })
app.mount('#app')
