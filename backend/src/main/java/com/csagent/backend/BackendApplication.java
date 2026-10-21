// 파일 역할: Spring Boot 백엔드 시작점; 컴포넌트 탐색과 서버 구동
package com.csagent.backend;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class BackendApplication {

    // 역할: Spring 애플리케이션 컨텍스트와 HTTP 서버 시작
    public static void main(String[] args) {
        SpringApplication.run(BackendApplication.class, args);
    }

}
