cmake_minimum_required(VERSION 3.20)
project(RecoilLedger CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(TP ${CMAKE_SOURCE_DIR}/third_party)

add_library(rl_model STATIC src/Model.cpp)
target_include_directories(rl_model PUBLIC src ${TP})

if(MSVC)
  add_compile_options(/utf-8 /MP)
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")  # static CRT: exe runs without redist
endif()

option(RL_BUILD_TESTS "Build trainer tests (fetches doctest)" OFF)
if(RL_BUILD_TESTS)
  include(FetchContent)
  FetchContent_Declare(doctest GIT_REPOSITORY https://github.com/doctest/doctest.git GIT_TAG v2.4.11)
  FetchContent_MakeAvailable(doctest)
  add_executable(rl_tests tests/test_trainer.cpp)
  target_link_libraries(rl_tests PRIVATE rl_model doctest::doctest)
  enable_testing()
  add_test(NAME rl_tests COMMAND rl_tests)
endif()

if(WIN32)
  add_library(imgui STATIC
    ${TP}/imgui/imgui.cpp ${TP}/imgui/imgui_draw.cpp ${TP}/imgui/imgui_tables.cpp ${TP}/imgui/imgui_widgets.cpp
    ${TP}/imgui/backends/imgui_impl_win32.cpp ${TP}/imgui/backends/imgui_impl_dx11.cpp)
  target_include_directories(imgui PUBLIC ${TP}/imgui ${TP}/imgui/backends)

  add_executable(RecoilLedger WIN32 src/main.cpp src/App.cpp src/Engine.cpp src/Overlay.cpp src/app.rc)
  target_include_directories(RecoilLedger PRIVATE ${TP}/stb)
  target_link_libraries(RecoilLedger PRIVATE rl_model imgui d3d11 dxgi dwmapi winmm shell32 ole32)
  target_compile_definitions(RecoilLedger PRIVATE UNICODE _UNICODE NOMINMAX)
  add_custom_command(TARGET RecoilLedger POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_directory ${CMAKE_SOURCE_DIR}/data $<TARGET_FILE_DIR:RecoilLedger>/data
    COMMAND ${CMAKE_COMMAND} -E copy_directory ${CMAKE_SOURCE_DIR}/assets $<TARGET_FILE_DIR:RecoilLedger>/assets)
endif()
