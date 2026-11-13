cmake_minimum_required(VERSION 3.24)
project(RecoilLedger CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include(FetchContent)
FetchContent_Declare(json URL https://github.com/nlohmann/json/releases/download/v3.11.3/json.tar.xz)
FetchContent_MakeAvailable(json)

add_library(rl_model STATIC src/Model.cpp)
target_include_directories(rl_model PUBLIC src)
target_link_libraries(rl_model PUBLIC nlohmann_json::nlohmann_json)

option(RL_BUILD_TESTS "Build trainer tests" ON)
if(RL_BUILD_TESTS)
  FetchContent_Declare(doctest GIT_REPOSITORY https://github.com/doctest/doctest.git GIT_TAG v2.4.11)
  FetchContent_MakeAvailable(doctest)
  add_executable(rl_tests tests/test_trainer.cpp)
  target_link_libraries(rl_tests PRIVATE rl_model doctest::doctest)
  enable_testing()
  add_test(NAME rl_tests COMMAND rl_tests)
endif()

if(WIN32)
  FetchContent_Declare(imgui GIT_REPOSITORY https://github.com/ocornut/imgui.git GIT_TAG v1.91.5)
  FetchContent_MakeAvailable(imgui)
  add_library(imgui STATIC
    ${imgui_SOURCE_DIR}/imgui.cpp ${imgui_SOURCE_DIR}/imgui_draw.cpp ${imgui_SOURCE_DIR}/imgui_tables.cpp
    ${imgui_SOURCE_DIR}/imgui_widgets.cpp ${imgui_SOURCE_DIR}/imgui_demo.cpp
    ${imgui_SOURCE_DIR}/backends/imgui_impl_win32.cpp ${imgui_SOURCE_DIR}/backends/imgui_impl_dx11.cpp)
  target_include_directories(imgui PUBLIC ${imgui_SOURCE_DIR} ${imgui_SOURCE_DIR}/backends)

  add_executable(RecoilLedger WIN32 src/main.cpp src/App.cpp src/Engine.cpp src/Overlay.cpp)
  target_link_libraries(RecoilLedger PRIVATE rl_model imgui d3d11 dxgi dwmapi winmm shell32 ole32)
  target_compile_definitions(RecoilLedger PRIVATE UNICODE _UNICODE)
  if(MSVC)
    target_compile_options(RecoilLedger PRIVATE /W4 /utf-8)
    target_compile_options(rl_model PRIVATE /utf-8)
  endif()
  add_custom_command(TARGET RecoilLedger POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_directory ${CMAKE_SOURCE_DIR}/data $<TARGET_FILE_DIR:RecoilLedger>/data)
endif()
