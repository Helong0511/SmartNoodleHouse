#ifndef AD_TRACKING_ENGINE_H
#define AD_TRACKING_ENGINE_H

#include <stdint.h>

#ifdef _WIN32
  #ifdef AD_TRACKING_ENGINE_EXPORTS
    #define AD_TRACKING_API __declspec(dllexport)
  #else
    #define AD_TRACKING_API __declspec(dllimport)
  #endif
#else
  #define AD_TRACKING_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define TRACKING_OK 0
#define TRACKING_ERR_NULL_INPUT 1
#define TRACKING_ERR_INVALID_ARGUMENT 2
#define TRACKING_ERR_KEY_INVALID 3
#define TRACKING_ERR_KEY_NOT_FOUND 4
#define TRACKING_ERR_CURRENT_KEY_NOT_FOUND 5
#define TRACKING_ERR_TOKEN_INVALID 6
#define TRACKING_ERR_TOKEN_TOO_LARGE 7
#define TRACKING_ERR_PAYLOAD_INVALID 8
#define TRACKING_ERR_EXPIRED 9
#define TRACKING_ERR_FUTURE_TIME 10
#define TRACKING_ERR_INTERNAL 100

AD_TRACKING_API int32_t tracking_set_key(uint8_t kid, const char *key_hex);
AD_TRACKING_API int32_t tracking_remove_key(uint8_t kid);
/* Clear all in-memory keys and current KID before unloading the DLL. */
AD_TRACKING_API int32_t tracking_clear_keys(void);
AD_TRACKING_API int32_t tracking_set_current_kid(uint8_t kid);

AD_TRACKING_API int32_t tracking_generate(const char *payload_json, uint64_t ttl_seconds, char **out_token);
AD_TRACKING_API int32_t tracking_verify(const char *token, char **out_json);
AD_TRACKING_API int32_t tracking_parse(const char *token, char **out_json);

/* The returned output pointer is owned by this library. The caller MUST call tracking_free(). */
AD_TRACKING_API void tracking_free(char *ptr);
/* Thread-local diagnostic string. Do not free it; valid until the next native call on this thread. */
AD_TRACKING_API const char *tracking_last_error(void);
AD_TRACKING_API const char *tracking_version(void);
AD_TRACKING_API uint32_t tracking_abi_version(void);

/* Returns JSON with CPU-aware worker/batch recommendations. Security limits are never reduced by hardware. */
AD_TRACKING_API int32_t tracking_performance_info(char **out_json);

#ifdef __cplusplus
}
#endif

#endif
