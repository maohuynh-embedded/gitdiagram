cmake_minimum_required(VERSION 3.20)
project(mini_ecu C)
add_executable(mini_ecu
  src/app/main.c
  src/drivers/can/can_driver.c
  src/hal/gpio.c)
target_include_directories(mini_ecu PRIVATE src/drivers/can src/hal)
