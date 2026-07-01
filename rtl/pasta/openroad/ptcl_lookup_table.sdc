create_clock -name clk -period 1.000 [get_ports clk]

set input_ports [remove_from_collection [all_inputs] [get_ports clk]]
set_input_delay 0.100 -clock clk $input_ports
set_output_delay 0.100 -clock clk [all_outputs]
