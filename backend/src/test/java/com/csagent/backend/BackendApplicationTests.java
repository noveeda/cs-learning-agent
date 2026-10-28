/**
 * @file
 * @brief Spring 설정과 의존성 주입으로 애플리케이션이 초기화되는지 검증
 */
package com.csagent.backend;

import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;

@SpringBootTest
class BackendApplicationTests {

    @Test
    /** @brief 서버 실행에 필요한 Spring 컨텍스트 구성 확인 */
    void contextLoads() {
    }

}
