cmake_minimum_required(VERSION 3.16)
project(tab5-app-files C)

set(CMAKE_C_STANDARD 11)

if(DEFINED ENV{TAB5_SDK_PATH})
  set(TAB5_SDK_DIR "$ENV{TAB5_SDK_PATH}")
elseif(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/../tab5-os/sdk/tab5-app-sdk")
  set(TAB5_SDK_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../tab5-os/sdk/tab5-app-sdk")
endif()

if(DEFINED TAB5_SDK_DIR)
  include("${TAB5_SDK_DIR}/cmake/Tab5AppConfig.cmake")
  find_package(Python3 REQUIRED COMPONENTS Interpreter)

  tab5_add_application(tab5_files
    SOURCES
      src/main.c
  )
else()
  message(WARNING "Tab5 SDK nao encontrado. Defina TAB5_SDK_PATH.")
endif()
